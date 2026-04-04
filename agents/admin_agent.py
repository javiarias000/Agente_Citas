#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ArcadiumAdminAgent - Agente especializado en tareas administrativas

Gestiona:
- CRUD de proyectos
- Gestión de usuarios (crear, modificar roles)
- Configuración de agentes por proyecto
- Estadísticas y reportes
- Gestión de conversations (toggle agente)
"""

from typing import Any, Dict, Optional, List
from datetime import datetime
import uuid
import structlog

from agents.langchain_compat import AgentExecutor, create_openai_tools_agent
from langchain_openai import ChatOpenAI
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from core.config import get_settings
from memory.memory_manager import MemoryManager

logger = structlog.get_logger("agent.admin")


class ArcadiumAdminAgent:
    """
    Agente administrativo para Arcadium

    Capacidades:
    - Gestión de proyectos (listar, crear, actualizar)
    - Gestión de usuarios (listar, crear, modificar)
    - Configuración de agentes por proyecto
    - Toggle de agentes por conversación
    - Consulta de estadísticas
    - Gestión de tool call logs
    """

    DEFAULT_SYSTEM_PROMPT = """
Eres Arcadium Admin, el agente administrativo de Arcadium Automation.

Tu rol:
1. Gestionar proyectos, usuarios y configuraciones
2. Supervisar el estado del sistema
3. Generar reportes y estadísticas
4. Administrar permisos y accesos
5. Gestionar tool call logs (audit trail)

Capacidades principales:

📊 GESTIÓN DE PROYECTOS:
- Listar todos los proyectos
- Crear nuevo proyecto (con API key única)
- Actualizar configuración de proyecto
- Activar/desactivar proyectos
- Ver estadísticas de proyecto (conversaciones, citas, tool calls)

👥 GESTIÓN DE USUARIOS:
- Listar usuarios del sistema
- Crear usuario con rol (admin, manager, agent, viewer)
- Modificar roles y permisos
- Asignar/remover usuario de proyectos
- Resetear contraseñas (generar enlace de recuperación)

🤖 CONFIGURACIÓN DE AGENTES:
- Ver/actualizar ProjectAgentConfig
- Habilitar/deshabilitar agentes por proyecto
- Configurar system prompt personalizado
- Ajustar temperatura, max_iterations
- Habilitar/deshabilitar Google Calendar por proyecto
- Configurar mapeo de servicios a odontólogos

📞 GESTIÓN DE CONVERSACIONES:
- Ver historial de conversación por teléfono
- Habilitar/deshabilitar agente para conversación específica (AgentToggle)
- Forzar uso de agente aunque esté deshabilitado globalmente

📈 ESTADÍSTICAS:
- Conversaciones activas
- Citas agendadas (por estado, por proyecto)
- Tool calls más utilizados
- Tiempos de respuesta promedio
- Errores recientes

⚠️ IMPORTANTE:
- Operaciones sensibles (borrar, modificar roles) requieren confirmación
- Cambios en configuraciones afectan a todos los usuarios del proyecto
- Guarda logs de cambios importantes (quién, cuándo, qué)

Responde en español, claro y conciso. Para operaciones sensibles, pide confirmación explícita.
""".strip()

    def __init__(
        self,
        session_id: str,
        system_prompt: Optional[str] = None,
        llm_model: str = None,
        llm_temperature: float = 0.7,
        verbose: bool = False
    ):
        self.session_id = session_id
        self.system_prompt = system_prompt or self.DEFAULT_SYSTEM_PROMPT
        self.llm_model = llm_model or get_settings().OPENAI_MODEL or "gpt-4"
        self.llm_temperature = llm_temperature
        self.verbose = verbose

        self.logger = logger.bind(
            agent="admin",
            session_id=session_id,
            model=self.llm_model
        )

        self._llm: Optional[ChatOpenAI] = None
        self._memory_manager: Optional[MemoryManager] = None
        self._agent_executor: Optional[AgentExecutor] = None
        self._initialized = False

    async def _initialize(self):
        if self._initialized:
            return self._agent_executor

        self.logger.info("Inicializando ArcadiumAdminAgent")

        # LLM
        self._llm = ChatOpenAI(
            model=self.llm_model,
            temperature=self.llm_temperature,
            api_key=get_settings().OPENAI_API_KEY
        )

        # Memory Manager para acceso a datos
        self._memory_manager = MemoryManager()
        await self._memory_manager.initialize()

        # Tools: por ahora solo las básicas (think, plan)
        # En el futuro, crear herramientas administrativas específicas
        try:
            from agents.deyy_agent import think, planificador_obligatorio
            tools = [think, planificador_obligatorio]
        except ImportError:
            tools = []

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

        # Memoria simple
        from langchain_core.messages import HumanMessage, AIMessage
        from collections import deque

        class SimpleMemory:
            def __init__(self, max_size=50):
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
            max_iterations=10,
            early_stopping_method="generate"
        )

        self._initialized = True
        self.logger.info("ArcadiumAdminAgent inicializado")

        return self._agent_executor

    async def run(
        self,
        input_text: str,
        conversation_history: Optional[List[Dict[str, Any]]] = None,
    ) -> Dict[str, Any]:
        """
        Procesa consulta administrativa

        Args:
            input_text: Mensaje del usuario (query admin)
            conversation_history: Historial (opcional)

        Returns:
            Dict con respuesta y metadata
        """
        start_time = datetime.utcnow()

        try:
            executor = await self._initialize()

            history = []
            if conversation_history:
                from langchain_core.messages import HumanMessage, AIMessage
                for msg in conversation_history[-10:]:
                    role = msg.get('role', 'human')
                    content = msg.get('content', '')
                    if role == 'human':
                        history.append(HumanMessage(content=content))
                    elif role == 'ai':
                        history.append(AIMessage(content=content))

            result = await executor.ainvoke({
                "input": input_text,
                "chat_history": history
            })

            execution_time = (datetime.utcnow() - start_time).total_seconds()

            return {
                "status": "success",
                "response": result.get("output", ""),
                "execution_time": execution_time,
                "agent_type": "admin"
            }

        except Exception as e:
            execution_time = (datetime.utcnow() - start_time).total_seconds()
            self.logger.error("Error ejecutando agente admin", error=str(e))

            return {
                "status": "error",
                "response": "Error procesando consulta administrativa.",
                "error": str(e),
                "execution_time": execution_time,
                "agent_type": "admin"
            }


# Factory
async def get_admin_agent(session_id: str, **kwargs) -> ArcadiumAdminAgent:
    """
    Crea ArcadiumAdminAgent

    Args:
        session_id: ID de sesión (usuario admin)
        **kwargs: Argumentos adicionales

    Returns:
        ArcadiumAdminAgent inicializado
    """
    agent = ArcadiumAdminAgent(session_id=session_id, **kwargs)
    await agent._initialize()
    return agent
