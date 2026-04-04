# -*- coding: utf-8 -*-
"""
Servicio de transcripción de audio con OpenAI Whisper
"""

import asyncio
from typing import Optional, Dict, Any
from dataclasses import dataclass
from pathlib import Path
import aiohttp
import tempfile
import structlog
from core.exceptions import TranscriptionError
from core.config import settings

logger = structlog.get_logger("transcriber")


@dataclass
class TranscriptionResult:
    """Resultado de transcripción"""
    text: str
    confidence: float
    duration: float
    language: str
    processing_time_ms: float
    model: str = "whisper-1"


class WhisperTranscriber:
    """Transcritor usando OpenAI Whisper API"""

    def __init__(
        self,
        api_key: str = None,
        model: str = "whisper-1",
        language: str = "es",
        timeout: int = 180
    ):
        self.api_key = api_key or settings.OPENAI_API_KEY
        if not self.api_key:
            raise TranscriptionError("OpenAI API key no configurada")

        self.model = model
        self.language = language
        self.timeout = timeout
        self.base_url = "https://api.openai.com/v1/audio/transcriptions"
        self.logger = logger

    async def transcribe(
        self,
        audio_data: bytes,
        filename: str = "audio.ogg",
        mime_type: str = "audio/ogg"
    ) -> TranscriptionResult:
        """
        Transcribe audio a texto

        Args:
            audio_data: Datos binarios del audio
            filename: Nombre de archivo
            mime_type: Tipo MIME del audio

        Returns:
            TranscriptionResult
        """
        start_time = asyncio.get_event_loop().time()

        try:
            async with aiohttp.ClientSession() as session:
                # Preparar formulario multipart
                form_data = aiohttp.FormData()
                form_data.add_field(
                    'file',
                    audio_data,
                    filename=filename,
                    content_type=mime_type
                )
                form_data.add_field('model', self.model)
                form_data.add_field('language', self.language)
                form_data.add_field('response_format', 'json')

                headers = {
                    'Authorization': f'Bearer {self.api_key}'
                }

                async with session.post(
                    self.base_url,
                    data=form_data,
                    headers=headers,
                    timeout=aiohttp.ClientTimeout(total=self.timeout)
                ) as response:

                    if response.status != 200:
                        error_text = await response.text()
                        raise TranscriptionError(
                            f"Error API Whisper: {response.status}",
                            response=error_text
                        )

                    result = await response.json()

            processing_time = (asyncio.get_event_loop().time() - start_time) * 1000

            return TranscriptionResult(
                text=result.get('text', '').strip(),
                confidence=result.get('confidence', 0.0),
                duration=result.get('duration', 0.0),
                language=result.get('language', self.language),
                processing_time_ms=processing_time,
                model=self.model
            )

        except asyncio.TimeoutError:
            raise TranscriptionError("Timeout en transcripción", timeout=self.timeout)
        except Exception as e:
            raise TranscriptionError(f"Error transcripción: {e}")

    async def transcribe_from_url(
        self,
        audio_url: str,
        phone: Optional[str] = None
    ) -> TranscriptionResult:
        """
        Transcribe audio desde URL

        Args:
            audio_url: URL del audio
            phone: Teléfono para logging

        Returns:
            TranscriptionResult
        """
        start_time = asyncio.get_event_loop().time()

        try:
            async with aiohttp.ClientSession() as session:
                # Descargar audio
                self.logger.info("Descargando audio", url=audio_url[:100], phone=phone)
                async with session.get(audio_url) as resp:
                    if resp.status != 200:
                        raise TranscriptionError(f"No se pudo descargar audio: {resp.status}")
                    audio_data = await resp.read()

            # Transcribir
            result = await self.transcribe(audio_data)
            result.processing_time_ms = (asyncio.get_event_loop().time() - start_time) * 1000

            self.logger.info(
                "Audio transcrito",
                phone=phone,
                text_length=len(result.text),
                confidence=result.confidence,
                time_ms=result.processing_time_ms
            )

            return result

        except Exception as e:
            self.logger.error("Error transcripción desde URL", error=str(e), phone=phone)
            raise

    async def transcribe_from_file(
        self,
        file_path: str,
        phone: Optional[str] = None
    ) -> TranscriptionResult:
        """Transcribe audio desde archivo local"""
        start_time = asyncio.get_event_loop().time()

        try:
            path = Path(file_path)
            if not path.exists():
                raise TranscriptionError(f"Archivo no existe: {file_path}")

            audio_data = path.read_bytes()
            result = await self.transcribe(audio_data, filename=path.name)
            result.processing_time_ms = (asyncio.get_event_loop().time() - start_time) * 1000

            return result

        except Exception as e:
            self.logger.error("Error transcripción desde archivo", error=str(e), phone=phone)
            raise


class TranscriptionCache:
    """Cache para transcripciones"""

    def __init__(self, state_manager, ttl_hours: int = 24):
        self.state = state_manager
        self.ttl = ttl_hours * 3600
        self.logger = logger.bind(component="transcription_cache")

    def _cache_key(self, phone: str, audio_hash: str) -> str:
        return f"transcription:{phone}:{audio_hash[:16]}"

    async def get(self, phone: str, audio_hash: str) -> Optional[TranscriptionResult]:
        """Obtiene transcripción cacheada"""
        key = self._cache_key(phone, audio_hash)
        cached = await self.state.get(key)
        if cached:
            self.logger.debug("Cache hit", phone=phone)
            return TranscriptionResult(**cached) if isinstance(cached, dict) else cached
        return None

    async def set(self, phone: str, audio_hash: str, result: TranscriptionResult) -> bool:
        """Guarda transcripción en cache"""
        key = self._cache_key(phone, audio_hash)
        data = {
            "text": result.text,
            "confidence": result.confidence,
            "duration": result.duration,
            "language": result.language,
            "processing_time_ms": result.processing_time_ms,
            "model": result.model
        }
        success = await self.state.set(key, data, ttl=self.ttl)
        self.logger.debug("Cache guardado", phone=phone, key=key[:16])
        return success


# Instancia global y función helper
_transcriber: Optional[WhisperTranscriber] = None
_cache: Optional[TranscriptionCache] = None


async def init_transcriber():
    """Inicializa transcriptor global"""
    global _transcriber, _cache
    from core.state import StateManager, MemoryStorage

    _transcriber = WhisperTranscriber()
    state = StateManager(MemoryStorage())
    _cache = TranscriptionCache(state)
    logger.info("Transcriptor inicializado")


async def transcribe_audio(
    audio_url: str,
    phone: str,
    use_cache: bool = True
) -> TranscriptionResult:
    """
    Función principal de transcripción (usada en landchains)

    Args:
        audio_url: URL del audio
        phone: Teléfono para logging y cache
        use_cache: Usar cache si está disponible

    Returns:
        TranscriptionResult con texto transcrito
    """
    global _transcriber, _cache

    if _transcriber is None:
        await init_transcriber()

    # Calcular hash simple del audio (usar URL como proxy si no hay datos)
    import hashlib
    audio_hash = hashlib.md5(audio_url.encode()).hexdigest()

    # Verificar cache
    if use_cache and _cache:
        cached = await _cache.get(phone, audio_hash)
        if cached:
            logger.info("Usando transcripción cacheada", phone=phone, text_len=len(cached.text))
            return cached

    # Transcribir
    result = await _transcriber.transcribe_from_url(audio_url, phone)

    # Guardar en cache
    if use_cache and _cache and result.text:
        await _cache.set(phone, audio_hash, result)

    return result
