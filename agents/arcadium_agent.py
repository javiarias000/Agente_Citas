# -*- coding: utf-8 -*-
"""
Agente Deyy - Agente LangChain especializado para Arcadium
Compatible con langchain==0.1.20
"""

import asyncio
from typing import Any, Dict, List, Optional
from datetime import datetime

from agents.langchain_compat import AgentExecutor, create_openai_tools_agent
from langchain_openai import ChatOpenAI
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder

import structlog

from core.config import settings
from utils.tools import get_deyy_tools
from utils.langchain_components import LangChainComponentFactory

logger = structlog.get_logger("agent.deyy")


class DeyyAgent:

    DEFAULT_SYSTEM_PROMPT = """
Eres Deyy, un asistente especializado en automatización de mensajería y gestión de conversaciones para Arcadium.

Tu rol:
1. Procesar mensajes de clientes
2. Analizar contexto
3. Generar respuestas útiles
4. Detectar intenciones
5. Gestionar agenda
6. Buscar en knowledge base

IMPORTANTE:
- Usa herramientas cuando sea necesario
- Mantén tono profesional y natural
""".strip()

    def __init__(
        self,
        session_id: str,
        system_prompt: Optional[str] = None,
        llm_model: str = None,
        llm_temperature: float = 0.7,
        memory_table: str = "langchain_memory_deyy",
        tools: Optional[List] = None,
        verbose: bool = False
    ):
        self.session_id = session_id
        self.system_prompt = system_prompt or self.DEFAULT_SYSTEM_PROMPT
        self.llm_model = llm_model or settings.OPENAI_MODEL or "gpt-4"
        self.llm_temperature = llm_temperature
        self.memory_table = memory_table
        self.verbose = verbose

        self.logger = logger.bind(
            agent="deyy",
            session_id=session_id,
            model=self.llm_model
        )

        self._llm: Optional[ChatOpenAI] = None
        self._memory = None
        self._tools: List = tools or []
        self._agent_executor: Optional[AgentExecutor] = None
        self._initialized = False

    async def _initialize(self):
        if self._initialized:
            return self._agent_executor

        self.logger.info("Inicializando agente Deyy")

        # LLM
        self._llm = LangChainComponentFactory.create_chat_model(
            model=self.llm_model,
            temperature=self.llm_temperature
        )

        # Memoria
        self._memory = LangChainComponentFactory.create_postgres_memory(
            session_id=self.session_id,
            table_name=self.memory_table
        )

        # Tools
        if not self._tools:
            vectorstore = None
            try:
                vectorstore = LangChainComponentFactory.create_supabase_vectorstore()
            except Exception as e:
                self.logger.warning("Vectorstore no disponible", error=str(e))

            self._tools = get_deyy_tools(
                vectorstore=vectorstore,
                llm=self._llm
            )

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

        # Executor
        self._agent_executor = AgentExecutor(
            agent=agent,
            tools=self._tools,
            memory=self._memory,
            verbose=self.verbose,
            handle_parsing_errors=True,
            max_iterations=10,
            early_stopping_method="generate"
        )

        self._initialized = True
        self.logger.info("Agente inicializado")

        return self._agent_executor

    async def run(
        self,
        input_text: str,
        conversation_history: Optional[List[Dict[str, Any]]] = None,
    ) -> Dict[str, Any]:

        start_time = datetime.utcnow()

        try:
            executor = await self._initialize()

            result = await executor.ainvoke({
                "input": input_text,
                "chat_history": conversation_history or []
            })

            execution_time = (datetime.utcnow() - start_time).total_seconds()

            tool_calls = self._extract_tool_calls(result)

            # Extraer reasoning si existe herramienta 'think'
            reasoning = None
            for call in tool_calls:
                if call["tool"] == "think" and call.get("observation"):
                    reasoning = call["observation"]
                    break

            return {
                "status": "success",
                "response": result.get("output", ""),
                "tool_calls": tool_calls,
                "execution_time": execution_time,
                **({"reasoning": reasoning} if reasoning else {})
            }

        except Exception as e:
            execution_time = (datetime.utcnow() - start_time).total_seconds()

            self.logger.error("Error ejecutando agente", error=str(e))

            # Si el agente no se pudo inicializar (no tiene executor), devolver "Agente no disponible"
            if not self._initialized:
                return {
                    "status": "error",
                    "response": "Agente no disponible",
                    "error": str(e),
                    "execution_time": execution_time
                }

            return {
                "status": "error",
                "response": "Error procesando mensaje",
                "error": str(e),
                "execution_time": execution_time
            }

    def _extract_tool_calls(self, result: Dict[str, Any]) -> List[Dict[str, Any]]:
        tool_calls = []

        for step in result.get("intermediate_steps", []):
            if len(step) >= 2:
                action, observation = step
                if hasattr(action, "tool"):
                    tool_input = getattr(action, "tool_input", {})
                    # Para herramienta 'think', extraer el campo 'thought' como string
                    if action.tool == 'think' and isinstance(tool_input, dict):
                        input_str = tool_input.get('thought', str(tool_input))
                    else:
                        input_str = str(tool_input)

                    tool_calls.append({
                        "tool": action.tool,
                        "input": input_str,
                        "observation": str(observation)[:200]
                    })

        return tool_calls

    async def reset(self):
        """Reinicia la memoria del agente (limpia historial)"""
        if self._memory is not None:
            if hasattr(self._memory, 'clear'):
                clear_method = self._memory.clear
                try:
                    # Intentar llamar como coroutine
                    result = clear_method()
                    if asyncio.iscoroutine(result):
                        await result
                except TypeError:
                    # Si clear() es sync, llamar sin await
                    clear_method()
            elif hasattr(self._memory, 'clear_memory'):
                clear_method = self._memory.clear_memory
                try:
                    result = clear_method()
                    if asyncio.iscoroutine(result):
                        await result
                except TypeError:
                    clear_method()
async def get_agent_response(phone: str, message: str) -> Dict[str, Any]:
    """
    Helper function para obtener respuesta del agente Deyy.
    
    Args:
        phone: Número de teléfono del usuario
        message: Mensaje del usuario
        
    Returns:
        Dict con status, response, tool_calls, execution_time
    """
    try:
        agent = DeyyAgent(session_id=phone)
        result = await agent.run(message)
        return result
    except Exception as e:
        logger.error("Error en get_agent_response", error=str(e))
        return {
            "status": "error",
            "response": "Agente no disponible",
            "error": str(e),
            "execution_time": 0.0
        }
