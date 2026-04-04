# Migración de DeyyAgent a StateGraph

## Cambios Requeridos

### 1. Reemplazar initialize()
```python
async def initialize(self):
    """Inicializa el agente con StateGraph (DeyyGraph)"""
    if self._initialized:
        return

    logger.info(
        "Inicializando DeyyAgent",
        project_id=str(self.project_id) if self.project_id else None
    )

    # Crear LLM
    self._llm = ChatOpenAI(
        model=self.llm_model,
        temperature=self.llm_temperature,
        api_key=get_settings().OPENAI_API_KEY,
        timeout=get_settings().OPENAI_TIMEOUT,
        max_retries=3
    )

    # Inicializar servicio de citas
    if self.project_config:
        self.appointment_service = ProjectAppointmentService(self.project_config)
    else:
        self.appointment_service = _get_appointment_service()

    # Crear DeyyGraph
    self._graph = await create_deyy_graph(
        session_id=self.session_id,
        store=self.store,
        project_id=self.project_id,
        system_prompt=self.system_prompt,
        llm_model=self.llm_model,
        llm_temperature=self.llm_temperature
    )

    self._initialized = True
    logger.info("DeyyAgent inicializado con StateGraph")
```

### 2. Reemplazar process_message()
```python
async def process_message(
    self,
    message: str,
    save_to_memory: bool = True,
    check_toggle: bool = True
) -> Dict[str, Any]:
    """
    Procesa un mensaje del usuario usando StateGraph.
    """
    start_time = datetime.utcnow()

    try:
        if not self._initialized:
            await self.initialize()

        # Extraer phone_number
        phone = self._extract_phone_from_session(self.session_id)

        # Context vars
        project_token = None
        if self.project_id:
            project_token = set_current_project(self.project_id, self.project_config)

        # Verificar toggle
        if check_toggle and self.project_id:
            toggle_enabled = await self._check_agent_toggle()
            if not toggle_enabled:
                logger.info("Agente deshabilitado", session_id=self.session_id)
                if save_to_memory:
                    await self.store.add_message(
                        self.session_id, message, "human", self.project_id
                    )
                return {
                    "status": "agent_disabled",
                    "response": "Lo siento, el agente está temporalmente deshabilitado.",
                    "agent_disabled": True
                }

        # Set phone context
        token = set_current_phone(phone)

        try:
            # 1. Cargar historial desde store
            history = await self.store.get_history(self.session_id)

            # 2. Crear estado inicial
            from graphs.deyy_graph import DeyyState
            state = DeyyState(
                messages=history,
                phone_number=phone,
                project_id=self.project_id
            )

            # 3. Añadir mensaje del usuario
            state["messages"].append(HumanMessage(content=message))

            # 4. Invocar StateGraph
            config = {"configurable": {"thread_id": self.session_id}}
            result = await self._graph.ainvoke(state, config=config)

            # 5. Extraer respuesta (último mensaje AI)
            response = ""
            if result.get("messages"):
                ai_messages = [m for m in result["messages"] if isinstance(m, AIMessage)]
                if ai_messages:
                    response = ai_messages[-1].content

            execution_time = (datetime.utcnow() - start_time).total_seconds()

            # 6. Guardar en memoria y actualizar perfil (ya lo hace after_agent_node)
            # No es necesario hacerlo aquí

            logger.info(
                "Mensaje procesado con StateGraph",
                session_id=self.session_id,
                execution_time=execution_time
            )

            return {
                "status": "success",
                "response": response,
                "execution_time_seconds": execution_time,
                "session_id": self.session_id
            }

        finally:
            reset_phone(token)
            if project_token:
                reset_project(project_token)

    except Exception as e:
        execution_time = (datetime.utcnow() - start_time).total_seconds()
        logger.error(
            "Error procesando mensaje",
            session_id=self.session_id,
            error=str(e),
            execution_time=execution_time
        )
        return {
            "status": "error",
            "response": "Lo siento, ocurrió un error procesando tu mensaje.",
            "error": str(e),
            "execution_time_seconds": execution_time,
            "session_id": self.session_id
        }
```

### 3. Eliminar métodos auxiliares no usados
- `_extract_tool_calls` ya no es necesario (StateGraph maneja tools automáticamente)
- ~~`_agent_executor`~~ → reemplazar por `_graph`

### 4. Actualizar imports
```python
# Antes:
from langchain.agents import AgentExecutor, create_openai_tools_agent
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder

# Después:
from graphs.deyy_graph import create_deyy_graph
```

### 5. Actualizar __init__
```python
self._graph: Optional[StateGraph] = None
# Eliminar: self._agent_executor
```

---
## Resumen
- ✅ DeyyGraph completo con 3 nodos: load → agent → after → END
- ✅ after_agent_node guarda mensajes y actualiza perfil
- ✅ Checkpointer PostgresSaver integrado
- ✅ Inyección de store en todos los nodos
- ✅ Manejo de errores robusto
"""

print(__doc__)
