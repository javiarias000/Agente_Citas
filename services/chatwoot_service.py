#!/usr/bin/env python3
"""
Servicio de Chatwoot
Envía y recibe mensajes a través de Chatwoot API
"""

from typing import Optional, Dict, Any, List
import httpx
import structlog
import hmac
import hashlib
from tenacity import (
    retry,
    stop_after_attempt,
    wait_exponential,
    retry_if_exception_type
)

from core.config import get_settings

logger = structlog.get_logger("chatwoot.service")


class ChatwootMessage:
    """Clase de valor para mensajes de Chatwoot"""

    def __init__(
        self,
        conversation_id: str,
        content: str,
        message_type: str = "outgoing",
        private: bool = False,
        sender_type: str = "agent"  # 'contact', 'agent', 'system'
    ):
        """
        Args:
            conversation_id: ID de la conversación en Chatwoot
            content: Contenido del mensaje (texto o URL)
            message_type: Tipo de mensaje ('text', 'image', 'file', etc.)
            private: Si el mensaje es privado (solo agente)
            sender_type: Tipo de remitente ('agent' para mensajes del sistema, 'contact' para usuarios)
        """
        self.conversation_id = conversation_id
        self.content = content
        self.message_type = message_type
        self.private = private
        self.sender_type = sender_type

    def to_payload(self) -> Dict[str, Any]:
        """Convierte a formato Chatwoot API"""
        payload = {
            "content": self.content,
            "message_type": self.message_type,
            "content_type": "text",
            "private": self.private,
            "sender_type": self.sender_type
        }
        return payload


class ChatwootError(Exception):
    """Excepción personalizada para errores de Chatwoot"""
    pass


class ChatwootService:
    """
    Servicio para integración con Chatwoot
    Maneja envío de mensajes y parseo de webhooks
    """

    def __init__(self, settings=None):
        self.settings = settings or get_settings()
        self.base_url = self.settings.CHATWOOT_API_URL.rstrip('/') if self.settings.CHATWOOT_API_URL else ""
        self.token = self.settings.CHATWOOT_API_TOKEN
        self.account_id = self.settings.CHATWOOT_ACCOUNT_ID
        self.webhook_secret = self.settings.CHATWOOT_WEBHOOK_SECRET
        self.client: Optional[httpx.AsyncClient] = None

        logger.info(
            "ChatwootService inicializado",
            base_url=self.base_url,
            account_id=self.account_id
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
            "Retry enviando a Chatwoot",
            attempt=retry_state.attempt_number,
            max_attempts=3
        )
    )
    async def send_message(
        self,
        message: ChatwootMessage
    ) -> Dict[str, Any]:
        """
        Envía mensaje a Chatwoot con retry automático

        Args:
            message: Objeto ChatwootMessage

        Returns:
            Respuesta de la API

        Raises:
            ChatwootError: Si falla después de reintentos
        """
        if not self.client:
            await self.connect()

        if not self.account_id:
            raise ChatwootError("CHATWOOT_ACCOUNT_ID no configurado")

        endpoint = f"{self.base_url}/api/v1/accounts/{self.account_id}/conversations/{message.conversation_id}/messages"
        headers = {
            "Content-Type": "application/json",
            "api_access_token": self.token  # Chatwoot usa api_access_token
        }

        # Remover token si no está configurado
        if not self.token:
            headers.pop("api_access_token", None)

        try:
            logger.info(
                "Enviando mensaje a Chatwoot",
                conversation_id=message.conversation_id,
                message_type=message.message_type
            )

            response = await self.client.post(
                endpoint,
                json=message.to_payload(),
                headers=headers
            )

            response.raise_for_status()
            result = response.json()

            logger.info(
                "Mensaje enviado exitosamente a Chatwoot",
                message_id=result.get("id"),
                conversation_id=message.conversation_id
            )

            return {
                "success": True,
                "message_id": result.get("id"),
                "status": "sent",
                "response": result
            }

        except httpx.HTTPStatusError as e:
            error_msg = f"HTTP {e.response.status_code}: {e.response.text}"
            logger.error("Error HTTP enviando a Chatwoot", error=error_msg)
            raise ChatwootError(error_msg) from e

        except httpx.RequestError as e:
            logger.error("Error de red enviando a Chatwoot", error=str(e))
            raise ChatwootError(f"Error de red: {str(e)}") from e

        except Exception as e:
            logger.error("Error inesperado enviando a Chatwoot", error=str(e))
            raise ChatwootError(f"Error inesperado: {str(e)}") from e

    async def send_text(
        self,
        conversation_id: str,
        text: str,
        private: bool = False
    ) -> Dict[str, Any]:
        """
        Envía mensaje de texto simple

        Args:
            conversation_id: ID de la conversación
            text: Mensaje de texto
            private: Si el mensaje es privado

        Returns:
            Resultado del envío
        """
        message = ChatwootMessage(
            conversation_id=conversation_id,
            content=text,
            message_type="outgoing",
            private=private
        )
        return await self.send_message(message)

    @staticmethod
    def parse_webhook_payload(payload: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """
        Extrae información relevante del webhook de Chatwoot.

        Args:
            payload: Payload completo del webhook

        Returns:
            Dict con datos normalizados o None si se debe ignorar
            {
                'event': str,  # 'message_created', etc.
                'conversation_id': str,
                'account_id': int,
                'inbox_id': int,
                'message_id': str,
                'message_type': str,
                'content': str,
                'sender_type': 'contact' | 'agent' | 'system',
                'contact': Dict,  # {phone_number, email, name, ...}
                'raw': original_payload
            }
        """
        try:
            event = payload.get("event")

            # Chatwoot envía los datos en la raíz, NO en payload.payload
            # Usar el payload completo si no hay sub-clave 'payload'
            if "payload" in payload:
                webhook_payload = payload.get("payload", {})
            else:
                webhook_payload = payload

            if not event:
                logger.warning(
                    "Webhook de Chatwoot inválido",
                    payload_keys=list(payload.keys()),
                    has_webhook_payload=bool(webhook_payload)
                )
                return None

            logger.debug(
                "Parseando webhook Chatwoot",
                payload_keys=list(webhook_payload.keys())
            )

            # Extraer datos principales
            conversation = webhook_payload.get("conversation", {})
            sender = webhook_payload.get("sender", {})
            meta = webhook_payload.get("meta", {})

            conversation_id = str(conversation.get("id")) if conversation else None
            account_id = webhook_payload.get("account_id") or conversation.get("account_id")
            inbox_id = webhook_payload.get("inbox_id") or conversation.get("inbox_id")
            message_id = str(webhook_payload.get("id", ""))
            message_type = webhook_payload.get("message_type", "text")
            content = webhook_payload.get("content", "")
            sender_type = sender.get("type", "contact")  # 'contact', 'agent', 'system'

            logger.debug(
                "Datos extraídos",
                conversation_id=conversation_id,
                account_id=account_id,
                inbox_id=inbox_id,
                message_id=message_id,
                message_type=message_type,
                content_preview=content[:50] if content else "",
                sender_type=sender_type
            )

            # ═══════════════════════════════════════════════════════════
            # FILTROS ANTI-LOOP (múltiples capas de defensa)
            # ═══════════════════════════════════════════════════════════

            # 1. Filtrar por tipo de evento (solo procesar mensajes creados)
            if event != "message_created":
                logger.info(
                    "Ignorando evento no manejado",
                    event=event,
                    message_id=message_id
                )
                return None

            # 2. Filtrar por message_type (solo incoming = del usuario)
            # outgoing = mensaje saliente del agente/bot
            if message_type != "incoming":
                logger.info(
                    "Ignorando mensaje saliente (anti-loop)",
                    message_type=message_type,
                    message_id=message_id,
                    sender_type=sender_type
                )
                return None

            # 3. Filtrar por sender_type (seguro adicional)
            # 'contact' = usuario, 'agent'/'agent_bot' = bot
            if sender_type in ("agent", "agent_bot", "system"):
                logger.info(
                    "Ignorando mensaje del agente por sender_type (anti-loop)",
                    sender_type=sender_type,
                    message_id=message_id
                )
                return None

            # 4. Validar datos requeridos
            if not conversation_id:
                logger.warning(
                    "Webhook de Chatwoot sin conversation_id",
                    conversation=conversation
                )
                return None

            if not content:
                logger.warning(
                    "Webhook de Chatwoot sin contenido",
                    message_id=message_id,
                    content_present=bool(content)
                )
                return None

            # Validar datos requeridos
            if not conversation_id:
                logger.warning(
                    "Webhook de Chatwoot sin conversation_id",
                    conversation=conversation
                )
                return None

            if not content:
                logger.warning(
                    "Webhook de Chatwoot sin contenido",
                    message_id=message_id,
                    content_present=bool(content)
                )
                return None

            # Extraer contacto
            contact = {
                "id": sender.get("id"),
                "name": sender.get("name"),
                "phone_number": sender.get("phone_number"),
                "email": sender.get("email"),
                "identifier": sender.get("identifier")
            }

            result = {
                "event": event,
                "conversation_id": conversation_id,
                "account_id": account_id,
                "inbox_id": inbox_id,
                "message_id": message_id,
                "message_type": message_type,
                "content": content,
                "sender_type": sender_type,
                "contact": contact,
                "meta": meta,
                "raw": payload
            }

            logger.info(
                "Webhook de Chatwoot parseado exitosamente",
                conversation_id=conversation_id,
                sender_type=sender_type,
                content_length=len(content)
            )

            return result

        except Exception as e:
            logger.error("Error parseando webhook de Chatwoot", error=str(e), exc_info=True)
            return None

    @staticmethod
    def normalize_contact(contact: Dict[str, Any]) -> str:
        """
        Normaliza un contacto de Chatwoot a un identificador único.

        Prefiere phone_number, fallback a email.

        Args:
            contact: Dict con datos del contacto (phone_number, email, identifier)

        Returns:
            Identificador normalizado (string)
        """
        phone = contact.get("phone_number", "")
        email = contact.get("email", "")
        identifier = contact.get("identifier", "")

        # Limpiar y normalizar
        if phone:
            # Normalizar formato internacional
            from utils.phone_utils import normalize_phone
            try:
                normalized = normalize_phone(phone)
                if normalized:
                    return normalized
            except Exception as e:
                logger.warning("Error normalizando phone", phone=phone, error=str(e))
                # Fallback al teléfono limpio
                return "".join(c for c in phone if c.isdigit())

        elif email:
            # Email se usa tal cual (lowercase, strip)
            return email.lower().strip()

        elif identifier:
            # Usar identifier directamente
            return str(identifier)

        else:
            raise ValueError("Contacto sin phone_number, email ni identifier")

    @staticmethod
    def verify_webhook_signature(
        payload: bytes,
        signature: str,
        secret: str
    ) -> bool:
        """
        Verifica la firma HMAC-SHA256 del webhook.

        Args:
            payload: Cuerpo del webhook (raw bytes)
            signature: Header X-Chatwoot-Signature
            secret: Secreto configurado en Chatwoot

        Returns:
            True si la firma es válida
        """
        if not secret or not signature:
            return False

        expected_signature = hmac.new(
            secret.encode('utf-8'),
            payload,
            hashlib.sha256
        ).hexdigest()

        # Chatwoot envía la firma como "sha256=<hash>"
        if signature.startswith("sha256="):
            signature = signature[7:]

        return hmac.compare_digest(expected_signature, signature)
