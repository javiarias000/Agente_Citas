#!/usr/bin/env python3
"""
Servicio de WhatsApp
Envía mensajes a través de Evolution API o Meta API
"""

from typing import Optional, Dict, Any, List
import httpx
import structlog
from tenacity import (
    retry,
    stop_after_attempt,
    wait_exponential,
    retry_if_exception_type
)

from core.config import get_settings

logger = structlog.get_logger("whatsapp.service")


class WhatsAppMessage:
    """Clase de valor para mensajes de WhatsApp"""

    def __init__(
        self,
        to: str,
        text: Optional[str] = None,
        media_url: Optional[str] = None,
        media_type: Optional[str] = None,
        buttons: Optional[List[Dict[str, str]]] = None,
        quoted_message_id: Optional[str] = None
    ):
        """
        Args:
            to: Número destino (formato: 1234567890 o 1234567890@s.whatsapp.net)
            text: Texto del mensaje
            media_url: URL de imagen/audio/video
            media_type: Tipo de media (image, audio, video, document)
            buttons: Botones interactivos [{id, text}]
            quoted_message_id: ID de mensaje al que se responde
        """
        self.to = self._normalize_phone(to)
        self.text = text
        self.media_url = media_url
        self.media_type = media_type
        self.buttons = buttons or []
        self.quoted_message_id = quoted_message_id

    def _normalize_phone(self, phone: str) -> str:
        """Normaliza número de teléfono a formato Evolution API"""
        # Quitar prefijo @s.whatsapp.net si existe
        if "@s.whatsapp.net" in phone:
            phone = phone.split("@")[0]

        # Quitar espacios, guiones, paréntesis
        phone = "".join(c for c in phone if c.isdigit())

        # Evolution API usa formato: 1234567890
        return phone

    def to_evolution_payload(self) -> Dict[str, Any]:
        """Convierte a formato Evolution API"""
        payload = {
            "number": self.to
        }

        if self.media_url and self.media_type:
            payload[self.media_type] = self.media_url
            if self.text:
                payload["caption"] = self.text
        elif self.text:
            payload["text"] = self.text

        if self.buttons:
            payload["buttons"] = [
                {"id": btn["id"], "text": btn["text"]}
                for btn in self.buttons[:3]  # Max 3 botones
            ]

        if self.quoted_message_id:
            payload["quotedMessageId"] = self.quoted_message_id

        return payload

    def to_meta_payload(self) -> Dict[str, Any]:
        """Convierte a formato Meta Cloud API"""
        # Implementar según necesidades de Meta API
        raise NotImplementedError("Meta API no implementada aún")


class WhatsAppService:
    """
    Servicio para envío de mensajes WhatsApp vía Evolution API
    Con retry automático y manejo de errores
    """

    def __init__(self, settings=None):
        self.settings = settings or get_settings()
        self.base_url = self.settings.WHATSAPP_API_URL
        self.token = self.settings.WHATSAPP_API_TOKEN
        self.instance_name = self.settings.WHATSAPP_INSTANCE_NAME
        self.client: Optional[httpx.AsyncClient] = None

        logger.info(
            "WhatsAppService inicializado",
            base_url=self.base_url,
            instance=self.instance_name
        )

    async def __aenter__(self):
        await self.connect()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.disconnect()

    async def connect(self) -> None:
        """Inicializa cliente HTTP"""
        if self.client is None:
            self.client = httpx.AsyncClient(
                timeout=httpx.Timeout(30.0),
                limits=httpx.Limits(max_keepalive_connections=10)
            )

    async def disconnect(self) -> None:
        """Cierra cliente HTTP"""
        if self.client:
            await self.client.aclose()
            self.client = None

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        retry=retry_if_exception_type((httpx.HTTPError, httpx.TimeoutException)),
        before_sleep=lambda retry_state: logger.warning(
            "Retry enviando WhatsApp",
            attempt=retry_state.attempt_number,
            max_attempts=3
        )
    )
    async def send_message(
        self,
        message: WhatsAppMessage
    ) -> Dict[str, Any]:
        """
        Envía mensaje por WhatsApp con retry automático

        Args:
            message: Objeto WhatsAppMessage

        Returns:
            Respuesta de la API

        Raises:
            WhatsAppError: Si falla después de reintentos
        """
        if not self.client:
            await self.connect()

        # Evolution API endpoint
        endpoint = f"{self.base_url}/message/sendText/{self.instance_name}"

        payload = message.to_evolution_payload()
        headers = {
            "Content-Type": "application/json",
            "apikey": self.token  # Solo si el token es requerido
        }

        # Remover apikey si no está configurada
        if not self.token:
            headers.pop("apikey", None)

        try:
            logger.info(
                "Enviando mensaje WhatsApp",
                to=message.to,
                has_text=bool(message.text),
                has_media=bool(message.media_url)
            )

            response = await self.client.post(
                endpoint,
                json=payload,
                headers=headers
            )

            response.raise_for_status()
            result = response.json()

            logger.info(
                "Mensaje enviado exitosamente",
                to=message.to,
                message_id=result.get("key", {}).get("id")
            )

            return {
                "success": True,
                "message_id": result.get("key", {}).get("id"),
                "status": result.get("status"),
                "response": result
            }

        except httpx.HTTPStatusError as e:
            error_msg = f"HTTP {e.response.status_code}: {e.response.text}"
            logger.error("Error HTTP enviando WhatsApp", error=error_msg)
            raise WhatsAppError(error_msg) from e

        except httpx.RequestError as e:
            logger.error("Error de red enviando WhatsApp", error=str(e))
            raise WhatsAppError(f"Error de red: {str(e)}") from e

        except Exception as e:
            logger.error("Error inesperado enviando WhatsApp", error=str(e))
            raise WhatsAppError(f"Error inesperado: {str(e)}") from e

    async def send_text(
        self,
        to: str,
        text: str,
        quoted_message_id: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Envía mensaje de texto simple

        Args:
            to: Número destino
            text: Mensaje de texto
            quoted_message_id: ID del mensaje a citar (opcional)

        Returns:
            Resultado del envío
        """
        message = WhatsAppMessage(
            to=to,
            text=text,
            quoted_message_id=quoted_message_id
        )
        return await self.send_message(message)

    async def send_buttons(
        self,
        to: str,
        text: str,
        buttons: List[Dict[str, str]]
    ) -> Dict[str, Any]:
        """
        Envía mensaje con botones

        Args:
            to: Número destino
            text: Mensaje de texto
            buttons: Lista de botones [{id, text}]

        Returns:
            Resultado del envío
        """
        message = WhatsAppMessage(
            to=to,
            text=text,
            buttons=buttons
        )
        return await self.send_message(message)

    async def send_image(
        self,
        to: str,
        image_url: str,
        caption: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Envía imagen

        Args:
            to: Número destino
            image_url: URL pública de la imagen
            caption: Texto opcional

        Returns:
            Resultado del envío
        """
        message = WhatsAppMessage(
            to=to,
            media_url=image_url,
            media_type="image",
            text=caption
        )
        return await self.send_message(message)


class WhatsAppError(Exception):
    """Excepción personalizada para errores de WhatsApp"""
    pass
