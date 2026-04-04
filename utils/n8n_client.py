# -*- coding: utf-8 -*-
"""
Cliente para interactuar con n8n workflows
Gestión robusta con reintentos y validación
"""

import aiohttp
import asyncio
from typing import Any, Dict, Optional, List
from datetime import datetime
from pathlib import Path
import structlog
from core.exceptions import APIError, WorkflowError
from core.state import StateKeys

logger = structlog.get_logger("n8n_client")


class N8nClient:
    """Cliente HTTP para n8n REST API"""

    def __init__(
        self,
        base_url: str,
        api_key: Optional[str] = None,
        timeout: int = 60,
        max_retries: int = 3
    ):
        self.base_url = base_url
        self.api_key = api_key
        self.timeout = timeout
        self.max_retries = max_retries
        self._session: Optional[aiohttp.ClientSession] = None

    async def __aenter__(self):
        await self._ensure_session()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.close()

    async def _ensure_session(self):
        """Asegura que existe sesión HTTP"""
        if self._session is None or self._session.closed:
            timeout = aiohttp.ClientTimeout(total=self.timeout)
            headers = {}
            if self.api_key:
                headers['X-N8N-API-KEY'] = self.api_key

            self._session = aiohttp.ClientSession(
                timeout=timeout,
                headers=headers
            )

    async def close(self):
        """Cierra sesión HTTP"""
        if self._session and not self._session.closed:
            await self._session.close()

    async def execute_webhook(
        self,
        webhook_path: str,
        payload: Dict[str, Any],
        method: str = "POST",
        wait_for_completion: bool = False
    ) -> Dict[str, Any]:
        """
        Ejecuta webhook de n8n

        Args:
            webhook_path: Ruta del webhook (ej: 'arcadium_unificado')
            payload: Datos a enviar
            method: Método HTTP
            wait_for_completion: Si espera ejecución completa

        Returns:
            Respuesta de n8n
        """
        url = f"{self.base_url.rstrip('/')}/webhook/{webhook_path.lstrip('/')}"

        for attempt in range(self.max_retries):
            try:
                await self._ensure_session()

                async with self._session.request(
                    method=method,
                    url=url,
                    json=payload,
                    headers={'Content-Type': 'application/json'}
                ) as response:

                    response_data = await response.json()

                    if response.status != 200:
                        raise APIError(
                            f"Error webhook n8n: {response.status}",
                            status_code=response.status,
                            endpoint=url,
                            response=str(response_data)
                        )

                    logger.info(
                        "Webhook ejecutado exitosamente",
                        webhook=webhook_path,
                        attempt=attempt + 1,
                        response_status=response_data.get('status', 'unknown')
                    )

                    return response_data

            except aiohttp.ClientError as e:
                logger.warning(f"Error conexión n8n (intento {attempt + 1}): {e}")
                if attempt == self.max_retries - 1:
                    raise APIError(f"Error conectando a n8n después de {self.max_retries} intentos")
                await asyncio.sleep(2 ** attempt)  # Exponential backoff

            except Exception as e:
                logger.error(f"Error inesperado en webhook: {e}")
                raise

    async def get_workflow(self, workflow_id: str) -> Dict[str, Any]:
        """Obtiene detalles de un workflow"""
        await self._ensure_session()
        url = f"{self.base_url}/api/v1/workflows/{workflow_id}"

        async with self._session.get(url) as response:
            if response.status != 200:
                raise WorkflowError(f"No se pudo obtener workflow {workflow_id}")
            return await response.json()

    async def list_workflows(self) -> List[Dict[str, Any]]:
        """Lista todos los workflows"""
        await self._ensure_session()
        url = f"{self.base_url}/api/v1/workflows"

        async with self._session.get(url) as response:
            if response.status != 200:
                raise APIError("No se pudo listar workflows")
            return await response.json()

    async def activate_workflow(self, workflow_id: str) -> bool:
        """Activa un workflow"""
        await self._ensure_session()
        url = f"{self.base_url}/api/v1/workflows/{workflow_id}/activate"

        async with self._session.post(url) as response:
            if response.status == 200:
                logger.info(f"Workflow {workflow_id} activado")
                return True
            else:
                logger.error(f"Error activando workflow: {response.status}")
                return False

    async def deactivate_workflow(self, workflow_id: str) -> bool:
        """Desactiva un workflow"""
        await self._ensure_session()
        url = f"{self.base_url}/api/v1/workflows/{workflow_id}/deactivate"

        async with self._session.post(url) as response:
            if response.status == 200:
                logger.info(f"Workflow {workflow_id} desactivado")
                return True
            else:
                logger.error(f"Error desactivando workflow: {response.status}")
                return False


class WorkflowExecutor:
    """Ejecutor de workflows n8n con gestión de estado"""

    def __init__(
        self,
        n8n_client: N8nClient,
        state_manager: 'StateManager',
        workflow_json_path: str
    ):
        self.client = n8n_client
        self.state = state_manager
        self.workflow_json_path = workflow_json_path
        self._workflow_config: Optional[Dict[str, Any]] = None
        self.logger = logger.bind(component="workflow_executor")

    async def load_workflow_config(self) -> Dict[str, Any]:
        """Carga configuración de workflow desde JSON"""
        if self._workflow_config is None:
            import json
            try:
                with open(self.workflow_json_path, 'r', encoding='utf-8') as f:
                    self._workflow_config = json.load(f)
                self.logger.info(f"Workflow cargado: {self._workflow_config.get('name')}")
            except Exception as e:
                raise WorkflowError(f"No se pudo cargar workflow: {e}")
        return self._workflow_config

    def get_webhook_mapping(self) -> Dict[str, str]:
        """
        Extrae mapeo de webhooks del workflow
        Retorna: {webhook_name: webhook_path}
        """
        config = self._workflow_config or {}
        mapping = {}
        for node in config.get('nodes', []):
            if node.get('type') == 'n8n-nodes-base.webhook':
                webhook_name = node.get('name', 'unknown')
                webhook_id = node.get('webhookId', '')
                mapping[webhook_name] = webhook_id
        return mapping

    async def execute_unified_arcadium(
        self,
        phone: str,
        message: str,
        user_name: str,
        account_id: int,
        conversation_id: int,
        message_type: str = "text",
        attachments: List[Dict] = None,
        wait_for_completion: bool = True
    ) -> Dict[str, Any]:
        """
        Ejecuta workflow unificado Arcadium

        Args:
            phone: Número telefónico
            message: Contenido del mensaje
            user_name: Nombre del usuario
            account_id: ID de cuenta Chatwoot
            conversation_id: ID de conversación
            message_type: Tipo de mensaje (text/audio/...)
            attachments: Lista de adjuntos
            wait_for_completion: Esperar finalización

        Returns:
            Resultado de ejecución
        """
        # Preparar payload
        payload = {
            "telefono": phone,
            "conversation": message,
            "user_name": user_name,
            "account_id": account_id,
            "conversation_id": conversation_id,
            "message_type": message_type,
            "attachments": attachments or []
        }

        # Validar payload antes de enviar
        self._validate_payload(payload)

        # Obtener path del webhook
        webhook_mapping = self.get_webhook_mapping()
        webhook_path = webhook_mapping.get('Webhook Chatwoot', 'arcadium_unificado')

        # Guardar estado de inicio
        state_key = StateKeys.last_webhook(phone)
        await self.state.set(state_key, {
            "payload": payload,
            "timestamp": datetime.utcnow().isoformat(),
            "webhook": webhook_path
        })

        # Ejecutar webhook
        try:
            result = await self.client.execute_webhook(
                webhook_path=webhook_path,
                payload=payload,
                wait_for_completion=wait_for_completion
            )

            # Guardar resultado
            processing_key = StateKeys.processing(conversation_id)
            await self.state.set(processing_key, {
                "status": "completed",
                "result": result,
                "completed_at": datetime.utcnow().isoformat()
            })

            self.logger.info(
                "Workflow unificado ejecutado",
                phone=phone,
                conversation_id=conversation_id,
                success=True
            )

            return result

        except Exception as e:
            # Guardar error en estado
            processing_key = StateKeys.processing(conversation_id)
            await self.state.set(processing_key, {
                "status": "failed",
                "error": str(e),
                "failed_at": datetime.utcnow().isoformat()
            })

            self.logger.error(
                "Workflow unificado falló",
                phone=phone,
                conversation_id=conversation_id,
                error=str(e)
            )
            raise

    async def execute_processing_arcadium(
        self,
        phone: str,
        message: str,
        conversation_id: int,
        account_id: int,
        is_audio: bool = False,
        audio_data: Optional[bytes] = None
    ) -> Dict[str, Any]:
        """
        Ejecuta workflow de procesamiento Arcadium

        Args:
            phone: Número telefónico
            message: Mensaje (o transcripción)
            conversation_id: ID conversación
            account_id: ID cuenta
            is_audio: Si es mensaje de audio
            audio_data: Datos de audio (si es audio)

        Returns:
            Resultado de procesamiento
        """
        payload = {
            "telefono": phone,
            "conversation": message,
            "conversation_id": conversation_id,
            "account_id": account_id,
            "esAudio": is_audio,
        }

        if is_audio and audio_data:
            # Para audio, se debe subir a storage primero
            audio_url = await self._upload_audio(audio_data, phone)
            payload["audio_url"] = audio_url

        # Mapeo de webhook
        webhook_mapping = self.get_webhook_mapping()
        webhook_path = webhook_mapping.get('Webhook Audio', 'arcadium')

        result = await self.client.execute_webhook(
            webhook_path=webhook_path,
            payload=payload
        )

        return result

    async def _upload_audio(self, audio_data: bytes, phone: str) -> str:
        """Sube audio a almacenamiento y retorna URL"""
        # TODO: Implementar subida a S3, MinIO o similar
        #Por ahora: guardar local y servir HTTP
        import uuid
        import os

        upload_dir = Path("/tmp/arcadium_audio")
        upload_dir.mkdir(parents=True, exist_ok=True)

        filename = f"{phone}_{uuid.uuid4().hex}.ogg"
        filepath = upload_dir / filename

        with open(filepath, 'wb') as f:
            f.write(audio_data)

        # TODO: Servir vía HTTP
        return f"file://{filepath}"

    def _validate_payload(self, payload: Dict[str, Any]) -> None:
        """Valida payload antes de envío"""
        # Validar required fields
        required = ['telefono', 'conversation', 'account_id', 'conversation_id']
        missing = [f for f in required if f not in payload or payload[f] is None]
        if missing:
            raise WorkflowError(f"Campos requeridos faltantes: {missing}")

        # Validar phone - aceptar formato internacional con +
        phone = str(payload['telefono'])
        # Limpiar: quitar espacios, guiones, paréntesis; mantener + si está al inicio
        import re
        phone_clean = re.sub(r'[^\d+]', '', phone)
        # Validar que después del + solo haya dígitos
        if phone_clean.startswith('+'):
            digits = phone_clean[1:]
            if not digits.isdigit() or len(digits) < 9:
                raise WorkflowError(f"Número telefónico inválido: {phone}")
        else:
            if not phone_clean.isdigit() or len(phone_clean) < 10:
                raise WorkflowError(f"Número telefónico inválido: {phone}")

        # Validar account_id y conversation_id
        if not isinstance(payload['account_id'], int) or payload['account_id'] <= 0:
            raise WorkflowError(f"Account ID inválido: {payload['account_id']}")
        if not isinstance(payload['conversation_id'], int) or payload['conversation_id'] <= 0:
            raise WorkflowError(f"Conversation ID inválido: {payload['conversation_id']}")

        logger.debug("Payload validado", **payload)
