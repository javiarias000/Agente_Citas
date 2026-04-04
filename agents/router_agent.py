#!/usr/bin/env python3
"""
Agente Router/Classifier.
Clasifica la intención y delega a agentes especializados.
"""

from typing import Optional, Any, Literal
from uuid import UUID

from core.store import ArcadiumStore
from db.models import ProjectAgentConfig

#from .appointment_agent import AppointmentAgent  # Reemplazado por StateMachineAgent
#from .reschedule_agent import RescheduleAgent  # TODO
#from .cancel_agent import CancelAgent          # TODO
#from .info_agent import InfoAgent            # TODO

from agents.state_machine_agent import StateMachineAgent

from utils.logger import get_logger

logger = get_logger("agent.router")


class RouterAgent:
    """
    Agente router que clasifica intenciones y delega a agentes especializados.
    No tiene state machine, solo clasifica y transfiere.
    """

    IntentType = Literal["agendar", "reagendar", "cancelar", "consultar", "otro"]

    def __init__(
        self,
        session_id: str,
        store: ArcadiumStore,
        project_id: Optional[UUID] = None,
        project_config: Optional[ProjectAgentConfig] = None,
        whatsapp_service: Optional[Any] = None,
        verbose: bool = False
    ):
        self.session_id = session_id
        self.store = store
        self.project_id = project_id
        self.project_config = project_config
        self.whatsapp_service = whatsapp_service
        self.verbose = verbose
        self._initialized = False

    def _get_default_prompt(self) -> str:
        return """Eres un clasificador de intenciones.

Tu única función: determinar qué quiere el usuario.

Intenciones posibles:
- "agendar": Quiere reservar una cita nueva (nueva, agendar, reservar, turno)
- "reagendar": Quiere modificar/cambiar una cita existente (cambiar, mover, reprogramar)
- "cancelar": Quiere eliminar una cita (cancelar, anular, eliminar)
- "consultar": Quiere ver disponibilidad o sus citas (ver, consultar, tengo, disponibilidad)
- "otro": Cualquier otra cosa (saludos, preguntas generales, gracias)

Responde SOLO con la intención: "agendar", "reagendar", "cancelar", "consultar" o "otro".
No agregues explicaciones, solo la palabra."""

    def _build_graph(self):
        """
        El Router no necesita state machine.
        Devuelve None para indicar que no usa graph.
        """
        return None

    async def initialize(self):
        """
        Inicializa el router. No crea graph, solo marca inicializado.
        """
        self._initialized = True
        logger.info("RouterAgent initialized (no graph, uses classification + delegation)")

    async def process_message(self, message: str) -> str:
        """
        Procesa mensaje: clasifica intención, crea agente especializado, delega.
        """
        if not self._initialized:
            await self.initialize()

        # 1. Clasificar intención
        intent = await self._classify_intent(message)
        logger.info("Intent detected", intent=intent, message=message[:50])

        # 2. Crear agente especializado
        specialized_agent = self._create_agent_for_intent(intent)

        if specialized_agent:
            # 3. Inicializar agente especializado
            await specialized_agent.initialize()

            # 4. Delegar mensaje
            try:
                response = await specialized_agent.process_message(message)
                return response
            except Exception as e:
                logger.error("Error in specialized agent", intent=intent, error=str(e))
                return f"Error procesando tu solicitud: {str(e)}"
        else:
            # Respuesta amigable para intenciones no soportadas
            return """😅 Lo siento, aún no puedo ayudarte con eso.

Lo que SÍ puedo hacer:
• Agendar una cita nueva
• Consultar disponibilidad
• Cancelar o reagendar citas

¿En qué puedo ayudarte?"""

    async def _classify_intent(self, message: str) -> str:
        """
        Clasifica la intención del usuario.
        Por ahora usa heurística simple, podría mejorarse con LLM o ML.
        """
        msg_lower = message.lower()

        # Palabras clave por intención
        agendar_keywords = ["cita", "agendar", "reservar", "turno", "hora", "programar", "apuntar"]
        reagendar_keywords = ["reagendar", "cambiar", "modificar", "mover", "reprogramar", "nueva fecha"]
        cancelar_keywords = ["cancelar", "eliminar", "anular", "quitar"]
        consultar_keywords = ["ver", "consultar", "disponible", "tengo", "citas", "horarios", "agenda"]

        # Contar coincidencias
        agendar_score = sum(1 for kw in agendar_keywords if kw in msg_lower)
        reagendar_score = sum(1 for kw in reagendar_keywords if kw in msg_lower)
        cancelar_score = sum(1 for kw in cancelar_keywords if kw in msg_lower)
        consultar_score = sum(1 for kw in consultar_keywords if kw in msg_lower)

        scores = {
            "agendar": agendar_score,
            "reagendar": reagendar_score,
            "cancelar": cancelar_score,
            "consultar": consultar_score
        }

        # Obtener la intención con mayor score
        max_intent = max(scores, key=scores.get)
        if scores[max_intent] == 0:
            return "otro"
        return max_intent

    def _create_agent_for_intent(self, intent: str) -> Optional[Any]:
        """
        Factory: crea el agente especializado según la intención.
        Por ahora, todos los casos usan StateMachineAgent (especializado único).
        """
        # Estado actual: solo usamos StateMachineAgent para todas las intenciones
        # Future: se puede expandir a diferentes agentes según intención
        AGENT_REGISTRY = {
            "agendar": StateMachineAgent,
            "reagendar": StateMachineAgent,
            "cancelar": StateMachineAgent,
            "consultar": StateMachineAgent,
            "otro": StateMachineAgent,  # Para saludos/general también usa state machine
        }

        agent_class = AGENT_REGISTRY.get(intent)
        if not agent_class:
            logger.warning("No agent registered for intent", intent=intent)
            return None

        return agent_class(
            session_id=self.session_id,
            store=self.store,
            project_id=self.project_id,
            project_config=self.project_config,
            verbose=self.verbose
        )
