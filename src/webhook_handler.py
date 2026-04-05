"""
Webhook handler — FastAPI endpoint + debounce Redis + Evolution API.

Recibe mensajes de WhatsApp, aplica debounce, llama al agente y responde.
"""

from typing import Any, Dict, Optional

import httpx
import structlog

logger = structlog.get_logger("langgraph.webhook")


class WebhookHandler:
    """
    Maneja el flujo completo del webhook de Evolution API.

    Uso:
        handler = WebhookHandler(agent_factory, redis_client, evolution_url, api_key)
        await handler.handle(payload)
    """

    def __init__(
        self,
        agent_factory,
        redis_client=None,
        evolution_url: str = "",
        api_key: str = "",
        debounce_seconds: float = 3.0,
    ):
        self.agent_factory = agent_factory
        self.redis = redis_client
        self.evolution_url = evolution_url
        self.api_key = api_key
        self.debounce_seconds = debounce_seconds

    async def handle(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """
        Punto de entrada principal.

        Flujo:
        1. Extrae datos del payload
        2. Aplica debounce Redis (3s)
        3. Normaliza teléfono
        4. Llama al agente
        5. Envía respuesta por Evolution API
        """
        try:
            # 1. Extraer datos
            phone = self._extract_phone(payload)
            message = self._extract_message(payload)

            if not phone or not message:
                logger.warning("Payload incompleto", payload=payload)
                return {"status": "ignored", "reason": "missing_data"}

            # 2. Debounce
            if self.redis:
                debounced = await self._check_debounce(phone, message)
                if debounced:
                    logger.info("Mensaje debounceado", phone=phone)
                    return {"status": "debounced"}

            # 3. Normalizar teléfono
            phone = self._normalize_phone(phone)

            # 4. Obtener/agente procesar
            agent = await self.agent_factory(phone)
            response = await agent.process_message(message)

            # 5. Enviar respuesta por WhatsApp
            if response.text:
                await self._send_response(phone, response.text)

            return {
                "status": "ok",
                "response": response.text,
                "appointment_id": response.appointment_id,
            }

        except Exception as e:
            logger.error("Error en webhook handler", error=str(e), exc_info=True)
            return {"status": "error", "error": str(e)}

    async def _check_debounce(self, phone: str, message: str) -> bool:
        """
        Si hay un mensaje previo dentro de debounce_seconds, retorna True.
        El último mensaje "gana", los anteriores se cancelan.
        """
        key = f"debounce:whatsapp:{phone}"
        try:
            existing = await self.redis.get(key)
            if existing:
                return True

            await self.redis.set(key, message, ex=int(self.debounce_seconds))
            return False
        except Exception as e:
            logger.warning("Error en debounce", error=str(e))
            return False

    @staticmethod
    def _extract_phone(payload: Dict[str, Any]) -> Optional[str]:
        """Extrae teléfono del payload de Evolution API."""
        data = payload.get("data", payload)
        return (
            data.get("key", {}).get("remoteJd")
            or data.get("key", {}).get("remote_jid")
            or data.get("phone_number")
            or data.get("phone")
        )

    @staticmethod
    def _extract_message(payload: Dict[str, Any]) -> Optional[str]:
        """Extrae texto del mensaje del payload de Evolution API."""
        data = payload.get("data", payload)
        return (
            data.get("message", {}).get("conversation")
            or data.get("message", {}).get("extendedTextMessage", {}).get("text")
            or data.get("message_text")
            or data.get("text")
        )

    @staticmethod
    def _normalize_phone(phone: str) -> str:
        """
        Normaliza teléfono a formato internacional.
        593999999999@s.whatsapp.net → +5939999999999
        """
        phone = phone.split("@")[0]
        if not phone.startswith("+"):
            phone = "+" + phone
        return phone

    async def _send_response(self, phone: str, text: str) -> bool:
        """Envía mensaje de respuesta vía Evolution API."""
        if not self.evolution_url or not self.api_key:
            logger.warning("No hay URL de Evolution API configurada")
            return False

        headers = {"Content-Type": "application/json", "apikey": self.api_key}
        body = {
            "number": phone.replace("+", ""),
            "text": text,
        }

        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.post(
                    f"{self.evolution_url}/message/sendText",
                    json=body,
                    headers=headers,
                )
                if resp.status_code == 200:
                    logger.info("Respuesta enviada", phone=phone)
                    return True
                else:
                    logger.warning(
                        "Error enviando respuesta",
                        status=resp.status_code,
                        body=resp.text,
                    )
                    return False
        except Exception as e:
            logger.error("Error en HTTP a Evolution API", error=str(e))
            return False
