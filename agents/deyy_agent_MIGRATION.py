import logging
from datetime import datetime
from typing import Any, Dict, List, Optional, TypedDict

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage
from langchain_openai import ChatOpenAI

# Tu graph
from graphs.deyy_graph import create_deyy_graph

logger = logging.getLogger(__name__)


# =========================
# 🧠 STATE (CLAVE)
# =========================
class DeyyState(TypedDict):
    messages: List[BaseMessage]
    phone_number: str
    project_id: Optional[str]


# =========================
# 🔧 UTILS
# =========================
def format_history(history_raw: List[Dict[str, Any]]) -> List[BaseMessage]:
    """
    Convierte historial de DB → mensajes de LangChain
    """
    formatted = []

    for msg in history_raw:
        role = msg.get("role") or msg.get("type")
        content = msg.get("message") or msg.get("content")

        if not content:
            continue

        if role in ["human", "user"]:
            formatted.append(HumanMessage(content=content))

        elif role in ["ai", "assistant"]:
            formatted.append(AIMessage(content=content))

    return formatted


# =========================
# 🤖 AGENTE
# =========================
class DeyyAgent:
    def __init__(
        self,
        session_id: str,
        store,
        project_id: Optional[str] = None,
        project_config: Optional[Dict] = None,
        system_prompt: Optional[str] = None,
        llm_model: str = "gpt-4o-mini",
        llm_temperature: float = 0.2,
    ):
        self.session_id = session_id
        self.store = store
        self.project_id = project_id
        self.project_config = project_config
        self.system_prompt = system_prompt

        self.llm_model = llm_model
        self.llm_temperature = llm_temperature

        self._initialized = False
        self._graph = None
        self._llm = None

    # =========================
    # 🚀 INIT
    # =========================
    async def initialize(self):
        if self._initialized:
            return

        logger.info("Inicializando DeyyAgent")

        # LLM
        self._llm = ChatOpenAI(
            model=self.llm_model, temperature=self.llm_temperature, max_retries=3
        )

        # GRAPH
        self._graph = await create_deyy_graph(
            session_id=self.session_id,
            store=self.store,
            project_id=self.project_id,
            system_prompt=self.system_prompt,
            llm_model=self.llm_model,
            llm_temperature=self.llm_temperature,
        )

        self._initialized = True
        logger.info("DeyyAgent listo con StateGraph")

    # =========================
    # 📩 PROCESS MESSAGE
    # =========================
    async def process_message(
        self, message: str, save_to_memory: bool = True, check_toggle: bool = True
    ) -> Dict[str, Any]:

        start_time = datetime.utcnow()

        try:
            if not self._initialized:
                await self.initialize()

            phone = self._extract_phone_from_session(self.session_id)

            # =========================
            # 1. HISTORIAL
            # =========================
            history_raw = await self.store.get_history(self.session_id)
            history = format_history(history_raw)

            # =========================
            # 2. STATE
            # =========================
            state: DeyyState = {
                "messages": history,
                "phone_number": phone,
                "project_id": self.project_id,
            }

            # =========================
            # 3. NUEVO MENSAJE
            # =========================
            state["messages"] = state["messages"] + [HumanMessage(content=message)]

            # =========================
            # 4. EJECUTAR GRAPH
            # =========================
            config = {"configurable": {"thread_id": self.session_id}}

            result = await self._graph.ainvoke(state, config=config)

            # =========================
            # 5. RESPUESTA
            # =========================
            messages = result.get("messages", [])

            ai_messages = [m for m in messages if isinstance(m, AIMessage)]

            response = ai_messages[-1].content if ai_messages else ""

            execution_time = (datetime.utcnow() - start_time).total_seconds()

            logger.info(
                "Mensaje procesado",
                session_id=self.session_id,
                execution_time=execution_time,
            )

            return {
                "status": "success",
                "response": response,
                "execution_time_seconds": execution_time,
                "session_id": self.session_id,
            }

        except Exception as e:
            execution_time = (datetime.utcnow() - start_time).total_seconds()

            logger.error(
                "Error procesando mensaje", error=str(e), session_id=self.session_id
            )

            return {
                "status": "error",
                "response": "Error procesando el mensaje",
                "error": str(e),
                "execution_time_seconds": execution_time,
                "session_id": self.session_id,
            }

    # =========================
    # 📞 HELPERS
    # =========================
    def _extract_phone_from_session(self, session_id: str) -> str:
        """
        Ajusta esto según tu formato real
        """
        if ":" in session_id:
            return session_id.split(":")[1]
        return session_id
