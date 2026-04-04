#!/usr/bin/env python3
"""
Clase base para agentes especializados.
Cada agente especializado tiene:
- State machine propio (graph)
- Prompt corto (solo tono)
- herramientas específicas
- Transiciones controladas por código
"""

from typing import Optional, Dict, Any
from uuid import UUID
from abc import ABC, abstractmethod

from core.store import ArcadiumStore
from memory.memory_manager import MemoryManager
from db.models import ProjectAgentConfig
from .deyy_agent import DeyyAgent  # Para herencia de funcionalidad base


class BaseSpecializedAgent(ABC):
    """
    Agente especializado con state machine propio.
    Contrario a DeyyAgent que tiene un prompt gigante, estos agentes:
    - Tienen prompts cortos (2-3 párrafos) solo para tono
    - La lógica de flujo está en el state machine (código)
    - Cada nodo del grafo es una función que decide transiciones
    """

    def __init__(
        self,
        session_id: str,
        store: ArcadiumStore,
        project_id: Optional[UUID] = None,
        project_config: Optional[ProjectAgentConfig] = None,
        system_prompt: Optional[str] = None,
        llm_model: str = "gpt-4o-mini",
        llm_temperature: float = 0.7,
        max_iterations: int = 10,
        verbose: bool = False
    ):
        self.session_id = session_id
        self.store = store
        self.project_id = project_id
        self.project_config = project_config
        self.llm_model = llm_model
        self.llm_temperature = llm_temperature
        self.max_iterations = max_iterations
        self.verbose = verbose

        # Prompt corto - cada subclase define el suyo
        self.system_prompt = system_prompt or self._get_default_prompt()

        # Inicialización diferida
        self._agent: Optional[DeyyAgent] = None
        self._initialized = False

    @abstractmethod
    def _get_default_prompt(self) -> str:
        """Prompt corto específico del agente."""
        pass

    @abstractmethod
    def _build_graph(self):
        """Construye el state graph específico de este agente."""
        pass

    async def initialize(self):
        """Inicializa el agente con su state graph."""
        if self._initialized:
            return

        # Crear DeyyAgent con prompt corto y graph especializado
        self._agent = DeyyAgent(
            session_id=self.session_id,
            store=self.store,
            project_id=self.project_id,
            system_prompt=self.system_prompt,
            llm_model=self.llm_model,
            llm_temperature=self.llm_temperature,
            max_iterations=self.max_iterations,
            verbose=self.verbose
        )

        # Construir graph especializado (sobrescribe el default)
        self._agent._graph = self._build_graph()

        # Inicializar
        await self._agent.initialize()
        self._initialized = True

    async def process_message(self, message: str) -> str:
        """Procesa un mensaje a través del agente."""
        if not self._initialized:
            await self.initialize()

        return await self._agent.process_message(message)

    def get_state(self) -> Dict[str, Any]:
        """Obtiene el estado actual."""
        if not self._agent:
            return {}
        return self._agent.get_state()

    def reset(self):
        """Resetea el agente."""
        if self._agent:
            self._agent.reset()
        self._initialized = False
