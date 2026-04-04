#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ArcadiumSupportAgent - Agente especializado en soporte general y consultas

Diferente de DeyyAgent (que está optimizado para citas dentales),
este agente maneja:
- Preguntas generales sobre servicios
- Consultas de precios
- Información de la clínica
- Troubleshooting básico
- Búsqueda en knowledge base
- Razonamiento complejo
- Planificación de tareas
"""

from typing import Any, Dict, Optional, List
from datetime import datetime
import structlog

from agents.langchain_compat import AgentExecutor, create_openai_tools_agent
from langchain_openai import ChatOpenAI
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from core.config import get_settings
from memory.memory_manager import MemoryManager
from utils.tools import get_deyy_tools

logger = structlog.get_logger("agent.support")


class ArcadiumSupportAgent:
    """
    Agente de soporte general para Arcadium

    Se especializa en:
    - Consultas informativas (horarios, servicios, precios)
    - Búsqueda en knowledge base
    - Razonamiento y planificación
    - Respuestas que NO requieren agendar/cancelar citas
    """

    DEFAULT_SYSTEM_PROMPT = """
Eres Arcadium Support, el agente de soporte oficial de Arcadium Automation.

Tu rol:
1. Responder preguntas sobre la plataforma Arcadium
2. Ayudar con troubleshooting de integraciones
3. Explicar funcionalidades y capacidades
4. Guiar en instalación y configuración
5. Responder preguntas sobre servicios de la clínica (precios, horarios, tratamientos)
6. Usar knowledge base para respuestas precisas

Personalidad:
- Experto técnico pero accesible
- Paciente y claro
- Proactivo en resolver dudas
- Precavido: si no sabes, admítelo

Capacidades (herramientas):
1. knowledge_base_search - Busca en docs oficiales
2. think - Razonamiento profundo
3. planificador_obligatorio - Planifica tareas
4. (Opcional: enviar_mensaje_whatsapp, update_user_profile)

Reglas:
- SIEMPRE usa knowledge_base_search para preguntas sobre la plataforma
- SIEMPRE usa think para problemas complejos
- SIEMPRE da respuestas prácticas y paso a paso
- NUNCA inventes información técnica
- SIEMPRE sugiere consultar docs oficiales si hay duda

Formato de respuesta:
- Claro y estructurado
- Usa bullets para listas
- Incluye ejemplos concretos
- Menciona versiones relevantes (si aplica)
- Proporciona enlaces a docs cuando sea relevante (pero no inventes URLs)

Si el usuario necesita agendar/consultar/cancelar citas:
- Derívalo al agente Deyy (sistema de citas)
- Di: "Para gestionar citas, necesitas hablar con Deyy, nuestro especialista en agenda."

Responde en español, tono profesional pero amigable.
""".strip()

    def __init__(
        self,
        session_id: str,
        system_prompt: Optional[str] = None,
        llm_model: str = None,
        llm_temperature: float = 0.7,
        verbose: bool = False,
        enable_calendar_tools: bool = False  # Por defecto, NO usa herramientas de citas
    ):
        self.session_id = session_id
        self.system_prompt = system_prompt or self.DEFAULT_SYSTEM_PROMPT
        self.llm_model = llm_model or get_settings().OPENAI_MODEL or "gpt-4"
        self.llm_temperature = llm_temperature
        self.verbose = verbose
        self.enable_calendar_tools = enable_calendar_tools

        self.logger = logger.bind(
            agent="support",
            session_id=session_id,
            model=self.llm_model
        )

        self._llm: Optional[ChatOpenAI] = None
        self._memory = None
        self._tools: List = []
        self._agent_executor: Optional[AgentExecutor] = None
        self._initialized = False

    async def _initialize(self):
        if self._initialized:
            return self._agent_executor

        self.logger.info("Inicializando ArcadiumSupportAgent")

        # LLM
        self._llm = ChatOpenAI(
            model=self.llm_model,
            temperature=self.llm_temperature,
            api_key=get_settings().OPENAI_API_KEY
        )

        # Tools: por defecto, solo knowledge, think, plan
        # Si enable_calendar_tools=True, también incluye citas y WhatsApp
        from agents.deyy_agent import (
            knowledge_base_search,
            think,
            planificador_obligatorio
        )

        tools = [knowledge_base_search, think, planificador_obligatorio]

        if self.enable_calendar_tools:
            # Importar herramientas de citas (pueden no estar disponibles)
            try:
                from agents.deyy_agent import (
                    agendar_cita,
                    consultar_disponibilidad,
                    obtener_citas_cliente,
                    cancelar_cita,
                    reagendar_cita
                )
                tools.extend([agendar_cita, consultar_disponibilidad, obtener_citas_cliente, cancelar_cita, reagendar_cita])
                logger.info("Herramientas de calendar agregadas a SupportAgent")
            except ImportError as e:
                logger.warning("No se pudieron cargar herramientas de calendar", error=str(e))

        # Opcional: WhatsApp si se necesita
        # try:
        #     from agents.deyy_agent import enviar_mensaje_whatsapp
        #     tools.append(enviar_mensaje_whatsapp)
        # except ImportError:
        #     pass

        self._tools = tools

        # Prompt
        prompt = ChatPromptTemplate.from_messages([
            ("system", self.system_prompt),
            MessagesPlaceholder(variable_name="chat_history"),
            ("human", "{input}"),
            MessagesPlaceholder(variable_name="agent_scratchpad")
        ])

        # Agente
        agent = create_openai_tools_agent(
            llm=self._llm,
            tools=self._tools,
            prompt=prompt
        )

        # Memoria simple (inmemory para soporte)
        from langchain_core.messages import HumanMessage, AIMessage
        from collections import deque

        class SimpleMemory:
            def __init__(self, max_size=20):
                self.messages = deque(maxlen=max_size)

            async def get_history(self, session_id: str) -> List:
                return list(self.messages)

            async def add_message(self, message):
                self.messages.append(message)

        memory = SimpleMemory()

        # Executor
        self._agent_executor = AgentExecutor(
            agent=agent,
            tools=self._tools,
            memory=memory,
            verbose=self.verbose,
            handle_parsing_errors=True,
            max_iterations=8,
            early_stopping_method="generate"
        )

        self._initialized = True
        self.logger.info("ArcadiumSupportAgent inicializado", tools_count=len(tools))

        return self._agent_executor

    async def run(
        self,
        input_text: str,
        conversation_history: Optional[List[Dict[str, Any]]] = None,
    ) -> Dict[str, Any]:
        """
        Procesa mensaje con el agente de soporte

        Args:
            input_text: Mensaje del usuario
            conversation_history: Historial previo (opcional)

        Returns:
            Dict con respuesta y metadata
        """
        start_time = datetime.utcnow()

        try:
            executor = await self._initialize()

            # Preparar historial
            if conversation_history:
                # Convertir a formato LangChain
                from langchain_core.messages import HumanMessage, AIMessage
                history = []
                for msg in conversation_history[-10:]:
                    role = msg.get('role', 'human')
                    content = msg.get('content', '')
                    if role == 'human':
                        history.append(HumanMessage(content=content))
                    elif role == 'ai':
                        history.append(AIMessage(content=content))
            else:
                history = []

            result = await executor.ainvoke({
                "input": input_text,
                "chat_history": history
            })

            execution_time = (datetime.utcnow() - start_time).total_seconds()

            return {
                "status": "success",
                "response": result.get("output", ""),
                "execution_time": execution_time,
                "agent_type": "support"
            }

        except Exception as e:
            execution_time = (datetime.utcnow() - start_time).total_seconds()
            self.logger.error("Error ejecutando agente de soporte", error=str(e))

            return {
                "status": "error",
                "response": "Lo siento, ocurrió un error procesando tu consulta. Por favor, intenta de nuevo.",
                "error": str(e),
                "execution_time": execution_time,
                "agent_type": "support"
            }


# Factory function
async def get_support_agent(
    session_id: str,
    enable_calendar_tools: bool = False,
    **kwargs
) -> ArcadiumSupportAgent:
    """
    Crea y retorna un ArcadiumSupportAgent

    Args:
        session_id: ID de sesión
        enable_calendar_tools: Si habilitar herramientas de citas
        **kwargs: Argumentes adicionales para el agente

    Returns:
        ArcadiumSupportAgent inicializado
    """
    agent = ArcadiumSupportAgent(
        session_id=session_id,
        enable_calendar_tools=enable_calendar_tools,
        **kwargs
    )
    await agent._initialize()
    return agent
