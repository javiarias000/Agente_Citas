# -*- coding: utf-8 -*-
"""
Cadenas de procesamiento específicas para Arcadium
Construye landchains para los diferentes flujos de trabajo
"""

import asyncio
from typing import Any, Dict, Optional, List
from datetime import datetime
import structlog
from core.landchain import LandChain, ChainLink, ChainResult, ChainStatus
from core.state import StateManager, StateKeys
from validators.schemas import (
    WebhookPayload, Conversation, Message, validate_required_fields,
    sanitize_text, ValidatorChain
)
from utils.n8n_client import WorkflowExecutor
from utils.transcriber import TranscriptionResult, transcribe_audio
from utils.langchain_components import LangChainComponentFactory
from agents.arcadium_agent import DeyyAgent
from chains.divisor_chain import DivisorChain
from core.exceptions import ConversationError, TranscriptionError

logger = structlog.get_logger("arcadium_chains")


class ArcadiumChainBuilder:
    """Constructor de cadenas para Arcadium"""

    def __init__(
        self,
        workflow_executor: WorkflowExecutor,
        state_manager: StateManager
    ):
        self.workflow_executor = workflow_executor
        self.state = state_manager
        self.logger = logger

        # Inicializar componentes LangChain (lazy)
        self._init_langchain_components()

    def _init_langchain_components(self):
        """Inicializa componentes LangChain desde configuración de workflow"""
        try:
            # Obtener configuración de workflow si está disponible
            workflow_config = self.workflow_executor._workflow_config or {}

            # Buscar nodo Agente_Deyy
            agent_node = None
            divisor_node = None
            for node in workflow_config.get('nodes', []):
                if node.get('name') == 'Agente_Deyy':
                    agent_node = node
                elif node.get('name') == 'Divisor_Mensajes':
                    divisor_node = node

            # Configurar Agente
            if agent_node:
                params = agent_node.get('parameters', {})
                self.agent_config = {
                    'system_prompt': params.get('text', DeyyAgent.DEFAULT_SYSTEM_PROMPT),
                    'prompt_type': params.get('promptType', 'chat'),
                    'options': params.get('options', {}),
                    'llm_model': params.get('options', {}).get('model', 'gpt-4')
                }
                self.logger.info("Configuración Agente_Deyy cargada desde workflow JSON")
            else:
                self.agent_config = {
                    'system_prompt': DeyyAgent.DEFAULT_SYSTEM_PROMPT,
                    'prompt_type': 'chat',
                    'options': {},
                    'llm_model': 'gpt-4'
                }
                self.logger.warning("Agente_Deyy no encontrado en workflow, usando defaults")

            # Configurar Divisor
            if divisor_node:
                params = divisor_node.get('parameters', {})
                batch_size = params.get('batching', {}).get('batchSize', 10)
                self.divisor_config = {'batch_size': batch_size}
            else:
                self.divisor_config = {'batch_size': 10}

            # Crear DivisorChain (una sola vez)
            self.divisor_chain = DivisorChain(**self.divisor_config)

            self.logger.info("Componentes LangChain inicializados", agent_config=self.agent_config)

        except Exception as e:
            self.logger.error("Error inicializando componentes LangChain", error=str(e))
            self.agent_config = None
            self.divisor_chain = None

    def build_unified_chain(self, strict: bool = True) -> LandChain:
        """
        Construye cadena unificada completa:
        1. Extracción y validación de payload
        2. Detección de tipo mensaje
        3. Transcripción si es audio
        4. Enriquecimiento de contexto
        5. Aplicación de reglas de negocio
        6. Ejecución de workflow n8n
        7. Actualización de estado
        """
        chain = LandChain(
            name="arcadium_unified",
            max_retries=3,
            timeout=300.0,
            strict_mode=strict,
            logger=structlog.get_logger("chain.arcadium_unified")
        )

        # 1. Extracción y validación
        chain.add_link(
            name="extract_and_validate",
            func=self._extract_and_validate,
            validator=self._validate_webhook_payload,
            max_retries=2,
            rollback_on_failure=True,
            rollback_func=self._rollback_extraction,
            metadata={"step": 1, "description": "Extracción y validación del payload"}
        )

        # 2. Detección de tipo mensaje
        chain.add_link(
            name="detect_message_type",
            func=self._detect_message_type,
            metadata={"step": 2, "description": "Detección tipo mensaje"}
        )

        # 3. Transcripción si es audio
        chain.add_link(
            name="transcribe_if_audio",
            func=self._transcribe_if_audio,
            validator=self._validate_transcription_needed,
            timeout=180.0,
            continue_on_failure=True,  # Si falla transcripción, continuar como texto
            metadata={"step": 3, "description": "Transcripción de audio"}
        )

        # 4. Enriquecimiento de contexto
        chain.add_link(
            name="enrich_context",
            func=self._enrich_context,
            metadata={"step": 4, "description": "Enriquecimiento de contexto"}
        )

        # 5. Validación de negocio
        chain.add_link(
            name="business_rules_validation",
            func=self._apply_business_rules,
            validator=self._validate_business_rules,
            continue_on_failure=False,
            metadata={"step": 5, "description": "Validación reglas de negocio"}
        )

        # 5.5. Ejecutar Agente Deyy (si está configurado)
        if self.agent_config:
            chain.add_link(
                name="execute_agent_deyy",
                func=self._execute_agent_deyy,
                timeout=300.0,
                continue_on_failure=True,  # Si agente falla, continuar sin él
                metadata={"step": 5.5, "description": "Ejecutar Agente Deyy"}
            )

        # 5.6. Aplicar Divisor de Mensajes (si hay divisor)
        if self.divisor_chain:
            chain.add_link(
                name="apply_divisor",
                func=self._apply_divisor,
                continue_on_failure=True,
                metadata={"step": 5.6, "description": "Dividir mensaje con Divisor_Mensajes"}
            )

        # 6. Ejecución workflow n8n
        chain.add_link(
            name="execute_n8n_workflow",
            func=self._execute_n8n_workflow,
            timeout=240.0,
            rollback_on_failure=True,
            rollback_func=self._rollback_workflow,
            metadata={"step": 6, "description": "Ejecución workflow n8n"}
        )

        # 7. Actualización de estado final
        chain.add_link(
            name="update_final_state",
            func=self._update_final_state,
            continue_on_failure=True,
            metadata={"step": 7, "description": "Actualización estado final"}
        )

        return chain

    def build_processing_chain(self, strict: bool = True) -> LandChain:
        """
        Construye cadena de procesamiento (para audio transcrito)
        """
        chain = LandChain(
            name="arcadium_processing",
            max_retries=2,
            timeout=180.0,
            strict_mode=strict,
            logger=structlog.get_logger("chain.arcadium_processing")
        )

        chain.add_link(
            name="load_conversation_state",
            func=self._load_conversation_state,
            metadata={"step": 1, "description": "Cargar estado conversación"}
        )

        chain.add_link(
            name="process_message",
            func=self._process_message,
            validator=self._validate_message_processing,
            metadata={"step": 2, "description": "Procesar mensaje"}
        )

        chain.add_link(
            name="generate_response",
            func=self._generate_response,
            metadata={"step": 3, "description": "Generar respuesta"}
        )

        chain.add_link(
            name="send_response",
            func=self._send_response,
            timeout=60.0,
            metadata={"step": 4, "description": "Enviar respuesta"}
        )

        chain.add_link(
            name="update_conversation_state",
            func=self._update_conversation_state,
            metadata={"step": 5, "description": "Actualizar estado conversación"}
        )

        return chain

    # ========== Funciones de eslabón ==========

    async def _extract_and_validate(self, payload: Dict[str, Any], context: Dict[str, Any]) -> Dict[str, Any]:
        """Eslabón 1: Extrae y valida payload"""
        try:
            webhook_payload = WebhookPayload(**payload)
            conversation = webhook_payload.extract_conversation()

            self.logger.info(
                "Payload extraído y validado",
                phone=conversation.phone,
                conversation_id=conversation.conversation_id,
                account_id=conversation.account_id,
                message_count=len(conversation.messages)
            )

            return {
                "conversation": conversation.dict(),
                "raw_payload": payload,
                "extraction_timestamp": datetime.utcnow().isoformat()
            }

        except Exception as e:
            self.logger.error("Error extrayendo payload", error=str(e))
            raise ConversationError(f"Error extrayendo conversación: {e}")

    def _validate_webhook_payload(self, data: Dict[str, Any]) -> None:
        """Validador para extracción"""
        # Validar que el payload tenga campos mínimos necesarios
        has_telephone = 'telefono' in data or ('body' in data and data['body'])
        has_account = 'account_id' in data
        has_conversation = 'conversation_id' in data

        if not has_telephone:
            raise ValueError("Falta número de teléfono en payload")
        if not has_account:
            raise ValueError("Falta account_id en payload")
        if not has_conversation:
            raise ValueError("Falta conversation_id en payload")

    async def _detect_message_type(self, data: Dict[str, Any], context: Dict[str, Any]) -> Dict[str, Any]:
        """Eslabón 2: Detecta tipo de mensaje"""
        conversation_data = data.get('conversation', {})
        messages = conversation_data.get('messages', [])

        if not messages:
            raise ConversationError("No hay mensajes en conversación")

        message = messages[0]
        attachments = message.get('attachments', [])

        # Determinar tipo
        if attachments:
            file_type = attachments[0].get('file_type', '')
            if file_type == 'audio':
                message_type = 'audio'
            elif file_type == 'image':
                message_type = 'image'
            else:
                message_type = 'file'
        else:
            message_type = 'text'

        self.logger.info(
            "Tipo mensaje detectado",
            message_type=message_type,
            has_attachments=bool(attachments)
        )

        data['message_type'] = message_type
        data['has_attachments'] = bool(attachments)
        return data

    async def _transcribe_if_audio(self, data: Dict[str, Any], context: Dict[str, Any]) -> Dict[str, Any]:
        """Eslabón 3: Transcribe audio si es necesario"""
        if data.get('message_type') != 'audio':
            self.logger.info("No es audio, omitiendo transcripción")
            data['transcription'] = None
            data['was_transcribed'] = False
            return data

        conversation_data = data.get('conversation', {})
        messages = conversation_data.get('messages', [])
        if not messages:
            return data

        message = messages[0]
        attachments = message.get('attachments', [])

        if not attachments:
            self.logger.warning("Mensaje tipo audio sin attachments")
            return data

        attachment = attachments[0]
        audio_url = attachment.get('url') or attachment.get('data')

        if not audio_url:
            self.logger.error("No se encontró URL/datos de audio")
            return data

        try:
            self.logger.info("Iniciando transcripción de audio", audio_url=audio_url[:100] + "...")
            transcription = await transcribe_audio(
                audio_url=audio_url,
                phone=data['conversation'].get('phone')
            )

            data['transcription'] = transcription.text
            data['transcription_confidence'] = transcription.confidence
            data['was_transcribed'] = True
            data['transcription_timestamp'] = datetime.utcnow().isoformat()

            # Reemplazar contenido del mensaje con transcripción
            conversation_data['messages'][0]['content'] = transcription.text
            conversation_data['messages'][0]['transcribed'] = True
            data['conversation'] = conversation_data

            self.logger.info(
                "Audio transcrito exitosamente",
                confidence=transcription.confidence,
                text_length=len(transcription.text)
            )

        except TranscriptionError as e:
            self.logger.error("Error transcribiendo audio", error=str(e))
            data['transcription_error'] = str(e)
            data['was_transcribed'] = False

        return data

    def _validate_transcription_needed(self, data: Dict[str, Any]) -> None:
        """Validador: verificar si se necesita transcripción"""
        if data.get('message_type') == 'audio':
            attachments = data.get('conversation', {}).get('messages', [{}])[0].get('attachments', [])
            if not attachments:
                raise ValueError("Mensaje audio sin attachments")

    async def _enrich_context(self, data: Dict[str, Any], context: Dict[str, Any]) -> Dict[str, Any]:
        """Eslabón 4: Enriquece contexto con estado previo"""
        conversation_data = data.get('conversation', {})
        phone = conversation_data.get('phone')

        if phone:
            # Cargar historial de conversación
            try:
                conversation_history = await self.state.get(
                    StateKeys.conversation(phone),
                    default=[]
                )

                data['conversation_history'] = conversation_history[-10:]  # Últimos 10 mensajes
                data['previous_context'] = self._extract_context_from_history(conversation_history)

            except Exception as e:
                self.logger.warning("Error cargando historial", error=str(e))
                data['conversation_history'] = []
                data['previous_context'] = {}

        # Agregar metadata
        data['enriched_at'] = datetime.utcnow().isoformat()
        data['processing_context'] = {
            "chain_id": context.get('chain_id'),
            "execution_id": context.get('execution_id'),
            "environment": "production"  # TODO: from config
        }

        return data

    async def _apply_business_rules(self, data: Dict[str, Any], context: Dict[str, Any]) -> Dict[str, Any]:
        """Eslabón 5: Aplica reglas de negocio"""
        rules_applied = []
        violations = []

        # Regla 1: Mensaje muy corto
        message_text = data.get('conversation', {}).get('messages', [{}])[0].get('content', '')
        if len(message_text.strip()) < 3:
            violations.append("mensaje_demasiado_corto")

        # Regla 2: Contenido sensible
        sensitive_words = ['error 500', 'exception', 'fallo crítico']
        if any(word in message_text.lower() for word in sensitive_words):
            rules_applied.append("detectado_contenido_sensible")

        # Regla 3: Prioridad por tipo
        if data.get('message_type') == 'audio':
            rules_applied.append("prioridad_audio")

        data['business_rules'] = {
            "applied": rules_applied,
            "violations": violations,
            "passed": len(violations) == 0
        }

        if violations and self.workflow_executor.workflow_json_path:
            raise ConversationError(
                f"Reglas de negocio violadas: {', '.join(violations)}",
                phone=data['conversation'].get('phone')
            )

        return data

    async def _execute_n8n_workflow(self, data: Dict[str, Any], context: Dict[str, Any]) -> Dict[str, Any]:
        """Eslabón 6: Ejecuta workflow n8n"""
        conversation = data.get('conversation', {})

        try:
            result = await self.workflow_executor.execute_unified_arcadium(
                phone=conversation.get('phone'),
                message=conversation.get('messages', [{}])[0].get('content', ''),
                user_name=conversation.get('user_name', ''),
                account_id=conversation.get('account_id', 0),
                conversation_id=conversation.get('conversation_id', 0),
                message_type=data.get('message_type', 'text'),
                attachments=conversation.get('messages', [{}])[0].get('attachments', []),
                wait_for_completion=True
            )

            data['n8n_result'] = result
            data['workflow_executed_at'] = datetime.utcnow().isoformat()

            self.logger.info(
                "Workflow n8n ejecutado",
                workflow="arcadium_unified",
                success=result.get('status') == 'success'
            )

            return data

        except Exception as e:
            self.logger.error("Error ejecutando workflow n8n", error=str(e))
            raise

    async def _update_final_state(self, data: Dict[str, Any], context: Dict[str, Any]) -> Dict[str, Any]:
        """Eslabón 7: Actualiza estado final"""
        conversation = data.get('conversation', {})
        phone = conversation.get('phone')
        conversation_id = conversation.get('conversation_id')

        if phone and conversation_id:
            # Guardar en historial
            try:
                history_key = StateKeys.conversation(phone)
                current_history = await self.state.get(history_key, default=[])

                # Agregar mensaje procesado
                message_record = {
                    "conversation_id": conversation_id,
                    "message": conversation.get('messages', [{}])[0],
                    "processed_at": datetime.utcnow().isoformat(),
                    "status": data.get('n8n_result', {}).get('status', 'unknown')
                }

                current_history.append(message_record)

                # Mantener máximo 100 mensajes
                if len(current_history) > 100:
                    current_history = current_history[-100:]

                await self.state.set(history_key, current_history, ttl=86400 * 30)  # 30 días

                self.logger.info(
                    "Estado actualizado",
                    phone=phone,
                    history_size=len(current_history)
                )

            except Exception as e:
                self.logger.error("Error actualizando estado", error=str(e))

        data['final_state_updated'] = True
        return data

    async def _rollback_extraction(self, data: Dict[str, Any], context: Dict[str, Any]):
        """Rollback para extracción"""
        self.logger.warning("Rollback extracción", phone=data.get('conversation', {}).get('phone'))

    async def _rollback_workflow(self, data: Dict[str, Any], context: Dict[str, Any]):
        """Rollback para workflow fallido"""
        conversation = data.get('conversation', {})
        self.logger.error(
            "Rollback workflow fallido",
            phone=conversation.get('phone'),
            conversation_id=conversation.get('conversation_id')
        )

        # TODO: Implementar compensación específica si es necesario
        # Por ejemplo: notificar error, revertir cambios, etc.

    def _extract_context_from_history(self, history: List[Dict]) -> Dict[str, Any]:
        """Extrae contexto del historial"""
        if not history:
            return {}

        last_messages = history[-3:]  # Últimos 3 mensajes

        return {
            "message_count": len(history),
            "last_interaction": last_messages[-1].get('processed_at') if last_messages else None,
            "previous_statuses": [m.get('status') for m in last_messages]
        }

    def _validate_business_rules(self, data: Dict[str, Any]) -> None:
        """Validador para reglas de negocio"""
        business_rules = data.get('business_rules', {})
        if business_rules.get('violations'):
            raise ValueError(f"Violaciones: {', '.join(business_rules['violations'])}")

    def _validate_message_processing(self, data: Dict[str, Any]) -> None:
        """Validador para procesamiento de mensaje"""
        if not data.get('conversation'):
            raise ValueError("Falta conversación")

    async def _load_conversation_state(self, data: Dict[str, Any], context: Dict[str, Any]) -> Dict[str, Any]:
        """Carga estado de conversación"""
        phone = data.get('phone') or context.get('phone')
        if phone:
            history = await self.state.get(StateKeys.conversation(phone), default=[])
            data['conversation_history'] = history
            data['previous_context'] = self._extract_context_from_history(history)
        return data

    async def _process_message(self, data: Dict[str, Any], context: Dict[str, Any]) -> Dict[str, Any]:
        """Procesa mensaje (lógica específica)"""
        # TODO: Implementar lógica de procesamiento IA
        message = data.get('message', '')
        data['processed_message'] = message.upper()  # Placeholder
        return data

    async def _generate_response(self, data: Dict[str, Any], context: Dict[str, Any]) -> Dict[str, Any]:
        """Genera respuesta"""
        # TODO: Integrar con LLM
        processed = data.get('processed_message', '')
        data['response'] = f"Respuesta automática a: {processed[:50]}..."
        return data

    async def _send_response(self, data: Dict[str, Any], context: Dict[str, Any]) -> Dict[str, Any]:
        """Envía respuesta a Chatwoot/WhatsApp"""
        # TODO: Implementar envío via API
        self.logger.info("Enviando respuesta", response=data.get('response'))
        data['response_sent'] = True
        return data

    async def _update_conversation_state(self, data: Dict[str, Any], context: Dict[str, Any]) -> Dict[str, Any]:
        """Actualiza estado de conversación"""
        phone = data.get('phone')
        if phone:
            await self.state.set(
                StateKeys.conversation(phone),
                data,
                ttl=86400 * 7  # 7 días
            )
        return data

    # ========== NUEVOS ESLABONES LANGCHAIN ==========

    async def _execute_agent_deyy(self, data: Dict[str, Any], context: Dict[str, Any]) -> Dict[str, Any]:
        """Eslabón: Ejecuta Agente Deyy para generar respuesta IA"""
        conversation = data.get('conversation', {})
        phone = conversation.get('phone') or conversation.get('telefono')
        message_text = conversation.get('messages', [{}])[0].get('content', '')
        history = data.get('conversation_history', [])

        if not self.agent_config:
            self.logger.warning("Agente no configurado (agent_config None), saltando ejecución")
            data['agent_result'] = None
            data['agent_response'] = None
            return data

        try:
            # Crear agente con session_id basado en phone
            session_id = f"deyy_{phone}"
            agent = DeyyAgent(
                session_id=session_id,
                system_prompt=self.agent_config.get('system_prompt'),
                llm_model=self.agent_config.get('llm_model', 'gpt-4'),
                llm_temperature=self.agent_config.get('options', {}).get('temperature', 0.7),
                verbose=False
            )

            # Preparar historial para agente: lista de {"role": "...", "content": "..."}
            chat_history = []
            for entry in history[-10:]:  # últimos 10 mensajes
                msg = entry.get('message', {})
                # Determinar role: human si no es agente, ai si es agente
                sender_type = msg.get('sender_type', '')
                role = 'ai' if 'bot' in sender_type.lower() or 'agent' in sender_type.lower() else 'human'
                content = msg.get('content', '')
                if content:
                    chat_history.append({"role": role, "content": content})

            # Ejecutar agente
            self.logger.info("Ejecutando Agente_Deyy", phone=phone, message_len=len(message_text))
            agent_response = await agent.run(
                input_text=message_text,
                conversation_history=chat_history
            )

            data['agent_result'] = agent_response
            data['agent_response'] = agent_response.get('response', '')
            data['agent_used_tools'] = agent_response.get('tool_calls', [])
            data['agent_reasoning'] = agent_response.get('reasoning')
            data['agent_status'] = agent_response.get('status')

            self.logger.info(
                "Agente_Deyy ejecutado",
                status=agent_response.get('status'),
                response_len=len(agent_response.get('response', '')),
                tools_used=len(agent_response.get('tool_calls', []))
            )

        except Exception as e:
            self.logger.error("Error ejecutando Agente_Deyy", error=str(e))
            data['agent_result'] = None
            data['agent_response'] = None
            data['agent_error'] = str(e)
            # No fallar la cadena, continuar (continue_on_failure)
            raise

        return data

    async def _apply_divisor(self, data: Dict[str, Any], context: Dict[str, Any]) -> Dict[str, Any]:
        """Eslabón: Divide mensaje usando Divisor_Mensajes (si hay respuesta del agente o mensaje original)"""
        # Usar respuesta del agente si existe; si no, usar mensaje original
        response_text = data.get('agent_response')
        if not response_text:
            conversation = data.get('conversation', {})
            response_text = conversation.get('messages', [{}])[0].get('content', '')

        if not response_text or not response_text.strip():
            self.logger.warning("No hay texto para dividir")
            data['divisor_parts'] = []
            data['divisor_count'] = 0
            return data

        try:
            self.logger.info("Aplicando Divisor_Mensajes", text_len=len(response_text))
            parts = await self.divisor_chain.process_single(response_text)

            data['divisor_parts'] = [p.dict() for p in parts]
            data['divisor_count'] = len(parts)

            # Log distribución de categorías y prioridades
            if parts:
                cats = {}
                for p in parts:
                    cat = p.categoria
                    cats[cat] = cats.get(cat, 0) + 1
                self.logger.info("División completada", parts=len(parts), categories=cats)

        except Exception as e:
            self.logger.error("Error en divisor", error=str(e))
            data['divisor_parts'] = []
            data['divisor_count'] = 0
            data['divisor_error'] = str(e)
            raise

        return data

