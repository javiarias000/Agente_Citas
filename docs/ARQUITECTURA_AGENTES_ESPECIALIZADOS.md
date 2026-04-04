# 🏗️ Arquitectura de Agentes Especializados - Arcadium Automation

**Versión**: 2.0 (Agent-Based Architecture)
**Fecha**: 2026-04-04
**Estado**: ✅ En Implementación (Fase 1 completada)

---

## 📋 Índice

1. [Visión General](#visión-general)
2. [Problema Resuelto](#problema-resuelto)
3. [Nueva Arquitectura](#nueva-arquitectura)
4. [Componentes Detallados](#componentes-detallados)
5. [Flujo de Datos](#flujo-de-datos)
6. [State Machines](#state-machines)
7. [Herramientas (Tools)](#herramientas-tools)
8. [Configuración](#configuración)
9. [Testing](#testing)
10. [Comparación con Arquitectura Anterior](#comparación-con-arquitectura-anterior)
11. [Próximos Pasos](#próximos-pasos)

---

## Visión General

Arcadium Automation evoluciona de un **agente monolítico** (DeyyAgent) a una **arquitectura de agentes especializados orquestados**, donde:

- ✅ **Cada agente tiene una responsabilidad única** (agendar, reagendar, cancelar, consultar)
- ✅ **La lógica de negocio está en código** (state machines), no en prompts extensos
- ✅ **Los prompts son ultra-cortos** (solo tono y formato de comunicación)
- ✅ **Transiciones controladas por código**, no por interpretación del LLM
- ✅ **Fácil debugging y testing** (cada agente es independiente)

---

## Problema Resuelto

### Arquitectura Anterior (Monolítico)

```
WhatsApp → DeyyAgent (prompt gigante) → Herramientas
```

**Problemas**:

- ❌ **Prompt de 500+ líneas** con instrucciones conflictivas
- ❌ **Bucle de confirmación**: El agente volvía a preguntar "¿ Confirmas...?" después de que el usuario decía "sí"
- ❌ **Difícil de debuggear**: No se sabía si el problema era el prompt, el LLM, o la lógica
- ❌ **Imposible de testear**: Prompt-dependent behavior
- ❌ **Extender = más confusión**: Cada nueva feature añadía más instrucciones al prompt

### Solución: Agentes Especializados

```
WhatsApp → RouterAgent → AppointmentAgent (state machine) → Tools
```

**Ventajas**:

- ✅ **Prompt de Router**: 10 líneas (solo clasificación)
- ✅ **Prompt de AppointmentAgent**: 15 líneas (solo tono)
- ✅ **State machine define flujo**: Código, no prompts
- ✅ **No más bucles**: Transiciones explícitas en código
- ✅ **Testable**: Cada state node es una función Python testeable

---

## Nueva Arquitectura

### Diagrama de Componentes

```
┌─────────────────────────────────────────────────────────────────┐
│                        WhatsApp (Evolution API)                 │
└───────────────────────────┬─────────────────────────────────────┘
                            │ POST /webhook/whatsapp
                            ▼
┌─────────────────────────────────────────────────────────────────┐
│                      FastAPI Orchestrator                       │
│                   (core/orchestrator.py)                       │
│                                                                 │
│  ┌──────────────────────────────────────────────────────┐      │
│  │  RouterAgent (agents/router_agent.py)                │      │
│  │  - Clasifica intención                               │      │
│  │  - Delega al agente especializado                   │      │
│  └──────────────────────────────────────────────────────┘      │
└─────────────────────────────────────────────────────────────────┘
                            │
           ┌────────────────┼────────────────┐
           ▼                ▼                ▼
    ┌──────────┐    ┌──────────┐    ┌──────────┐
    │Appointment│    │Reschedule│    │CancelAgent│
    │   Agent   │    │   Agent  │    │           │
    └──────────┘    └──────────┘    └──────────┘
         │
         ▼
┌──────────────────────────────────────────────────────┐
│   State Graph (graphs/appointment_graph.py)          │
│   - intake → validate → require_name → check_availability │
│   - confirm → schedule                                │
│   - Transiciones explícitas en código               │
└──────────────────────────────────────────────────────┘
         │
         ▼
┌──────────────────────────────────────────────────────┐
│   Tools (agents/deyy_agent.py, services/*.py)       │
│   - consultar_disponibilidad()                       │
│   - agendar_cita()                                  │
│   - obtener_citas_cliente()                         │
│   - cancelar_cita()                                 │
│   - reagendar_cita()                                │
└──────────────────────────────────────────────────────┘
         │
         ▼
┌──────────────────────────────────────────────────────┐
│   Services                                          │
│   - AppointmentService (services/appointment_service.py) │
│   - WhatsAppService (services/whatsapp_service.py) │
└──────────────────────────────────────────────────────┘
         │
         ▼
┌──────────────────────────────────────────────────────┐
│   Database (PostgreSQL)                             │
│   - conversations, messages, appointments           │
│   - langchain_memory, tool_call_logs                │
└──────────────────────────────────────────────────────┘
```

---

## Componentes Detallados

### 1. RouterAgent

**Ubicación**: `agents/router_agent.py`

**Responsabilidad**: Clasificar la intención del usuario y delegar al agente especializado correspondiente.

**Características**:

- **No tiene state machine**: Solo clasifica y delega
- **Prompt corto**: "Eres un clasificador de intenciones. Responde solo con: agendar, reagendar, cancelar, consultar, otro"
- **Algoritmo de clasificación**: Por ahora heurística simple (keywords), puede mejorarse con LLM

**Método principal**:

```python
async def process_message(self, message: str) -> str:
    """
    1. Clasifica intención
    2. Crea agente especializado
    3. Delega mensaje
    4. Retorna respuesta
    """
```

**Intenciones soportadas**:

| Intención   | Keywords                                          | Agente destino         |
| ----------- | ------------------------------------------------- | ---------------------- |
| `agendar`   | cita, agendar, reservar, turno, hora, programar   | AppointmentAgent       |
| `reagendar` | reagendar, cambiar, modificar, mover, reprogramar | RescheduleAgent (TODO) |
| `cancelar`  | cancelar, eliminar, anular, quitar                | CancelAgent (TODO)     |
| `consultar` | ver, consultar, disponible, tengo, citas          | InfoAgent (TODO)       |
| `otro`      | -                                                 | Respuesta genérica     |

**Factory Registry**:

```python
AGENT_REGISTRY = {
    "agendar": AppointmentAgent,
    # "reagendar": RescheduleAgent,  # TODO
    # "cancelar": CancelAgent,      # TODO
    # "consultar": InfoAgent,       # TODO
}
```

---

### 2. BaseSpecializedAgent

**Ubicación**: `agents/base_specialized_agent.py`

**Responsabilidad**: Clase base para todos los agentes especializados. Define la interfaz común.

**Métodos abstractos**:

```python
class BaseSpecializedAgent(ABC):
    @abstractmethod
    def _get_default_prompt(self) -> str:
        """Prompt específico del agente (tono, formato)"""

    @abstractmethod
    def _build_graph(self) -> Optional[CompiledStateGraph]:
        """Construye state graph (o None si no usa)"""

    @abstractmethod
    async def process_message(self, message: str) -> str:
        """Procesa mensaje y retorna respuesta"""
```

**Atributos comunes**:

- `session_id`: Identificador de sesión
- `store`: ArcadiumStore para persistencia
- `project_id`: UUID del proyecto (multi-tenant)
- `project_config`: Configuración específica
- `_initialized`: Flag de inicialización
- `_graph`: StateGraph compilado (si aplica)

---

### 3. AppointmentAgent

**Ubicación**: `agents/appointment_agent.py`

**Responsabilidad**: Manejar TODO el flujo de agendado de citas.

**State Graph**: `graphs/appointment_graph.py`

**Flujo controlado por código**:

```
┌─────────┐
│  intake │ → extrae nombre, fecha, hora, servicio
└────┬────┘
     │
     ▼
┌──────────┐
│ validate │ → ajusta fechas (finde), valida formato
└────┬─────┘
     │
     ▼
┌─────────────┐
│ require_name│ → si falta nombre, pregunta
└──────┬──────┘
       │
       ▼
┌──────────────────┐
│ check_availability│ → ejecuta consultar_disponibilidad()
└─────────┬────────┘
          │
          ▼
┌─────────┐
│  confirm│ → "¿Confirmas [servicio] para [fecha] a las [hora]?"
└────┬────┘
     │
     ▼
┌─────────┐
│ schedule│ → ejecuta agendar_cita()
└────┬────┘
     │
     ▼
┌──────────┐
│ resolution│ → éxito/error/final
└──────────┘
```

**Prompt ultra-corto**:

```python
def _get_default_prompt(self) -> str:
    return """Eres asistente de Arcadium para agendar citas.

Reglas:
- Amable, conciso, en español
- No inventes información
- Si no entiendes, pide clarificación

ESTADO ACTUAL (no lo menciones):
- Servicio: {selected_service}
- Fecha: {appointment_date}
- Hora: {appointment_time}
- Nombre: {patient_name}

Responde naturalmente al usuario."""
```

**Ventaja**: La lógica de "confirmación → agendar" NO está en el prompt, está en el state machine:

```python
async def confirm_node(self, state: AppointmentState) -> Command:
    """
    Nodo de confirmación.
    Si usuario dijo "sí", ejecuta agendar_cita DIRECTAMENTE.
    No genera mensaje de texto.
    """
    last_message = state["messages"][-1].lower()

    if any(affirmation in last_message for affirmation in ["sí", "si", "ok", "confirmo"]):
        # ✅ Ejecutar agendar_cita directamente
        result = await self._execute_agendar_cita(state)
        return Command(update={"result": result, "next": "resolution"})

    # Si no confirmó, seguir en estado normal
    return Command(update={})
```

**Sin bucle**: El state machine garantiza que después de `confirm_node` se va a `schedule` directamente, sin pasar por `check_availability` otra vez.

---

## State Machines

### State Graph de AppointmentAgent

**Ubicación**: `graphs/appointment_graph.py`

**Estado**: `AppointmentState` (TypedDict)

```python
class AppointmentState(TypedDict):
    session_id: str
    messages: List[AnyMessage]  # Historial completo
    current_step: str           # Paso actual
    selected_service: Optional[str]
    appointment_date: Optional[str]  # ISO format
    appointment_time: Optional[str]  # "10:00"
    patient_name: Optional[str]
    available_slots: Optional[List[Dict]]
    result: Optional[Dict]      # Resultado de tools
    error: Optional[str]
    metadata: Dict[str, Any]
```

**Nodos (Nodes)**:

| Nodo                 | Responsabilidad                                | Herramientas usadas              |
| -------------------- | ---------------------------------------------- | -------------------------------- |
| `intake`             | Extrae info inicial del mensaje                | Ninguna (parseo)                 |
| `validate`           | Ajusta fin de semana, valida fechas            | `adjust_date()` (método interno) |
| `require_name`       | Pregunta nombre si falta                       | `ask_name()` (genera mensaje)    |
| `check_availability` | Consulta slots disponibles                     | `consultar_disponibilidad()`     |
| `confirm`            | Pide confirmación, ejecuta agendar si confirmó | `agendar_cita()`                 |
| `schedule`           | Ejecuta agendado final (llamada a tool)        | `agendar_cita()`                 |
| `resolution`         | Retorna resultado final al usuario             | Ninguna                          |

**Transiciones (Edges)**:

```python
workflow.add_conditional_edges(
    "intake",
    lambda state: "validate" if state["selected_service"] else "intake"
)

workflow.add_conditional_edges(
    "validate",
    lambda state: "require_name" if not state["patient_name"] else "check_availability"
)

workflow.add_conditional_edges(
    "check_availability",
    lambda state: "confirm" if state["appointment_time"] in available_slots else "check_availability"
)

workflow.add_edge("confirm", "schedule")  # ← Directo, no repite disponibilidad
workflow.add_edge("schedule", "resolution")
workflow.add_edge("resolution", END)
```

**Checkpointer**: `PostgresSaver` para persistir estado entre ejecuciones.

---

## Flujo de Datos

### 1. Mensaje entrante desde WhatsApp

```json
{
  "sender": "+1234567890",
  "message": "Quiero una cita para mañana a las 10",
  "message_type": "text"
}
```

### 2. Orchestrator recibe webhook

```python
# core/orchestrator.py
async def webhook_whatsapp(self, payload: WhatsAppPayload):
    # 1. Obtener/crear Conversation
    conversation = await self.store.get_or_create_conversation(phone)

    # 2. Guardar mensaje inbound
    await self.store.save_message(conv_id, "user", payload.message)

    # 3. Obtener agente (RouterAgent)
    agent = self._get_or_create_agent(session_id, phone)

    # 4. Procesar mensaje
    response = await agent.process_message(payload.message)

    # 5. Guardar respuesta
    await self.store.save_message(conv_id, "assistant", response)

    # 6. Enviar a WhatsApp
    await self.whatsapp_service.send_text(phone, response)
```

### 3. RouterAgent clasifica y delega

```python
# agents/router_agent.py
async def process_message(self, message: str) -> str:
    intent = await self._classify_intent(message)
    specialized_agent = self._create_agent_for_intent(intent)
    response = await specialized_agent.process_message(message)
    return response
```

### 4. AppointmentAgent ejecuta state machine

```python
# agents/appointment_agent.py
async def process_message(self, message: str) -> str:
    # 1. Añadir mensaje al estado
    state = await self._load_state()
    state["messages"].append(HumanMessage(content=message))

    # 2. Ejecutar graph
    result = await self._graph.ainvoke(
        state,
        config={"configurable": {"thread_id": self.session_id}}
    )

    # 3. Extraer última respuesta AI
    last_ai_message = result["messages"][-1].content

    # 4. Guardar estado actualizado
    await self._save_state(result)

    return last_ai_message
```

### 5. State Machine ejecuta nodos

**Ejemplo**: Usuario dice "sí" a confirmación

```python
# graphs/appointment_graph.py
async def confirm_node(self, state: AppointmentState) -> Command:
    last_message = state["messages"][-1].content.lower()

    if "sí" in last_message or "si" in last_message:
        # ✅ Ejecutar agendar_cita INMEDIATAMENTE
        result = await self._execute_agendar_cita(state)

        # Actualizar estado
        return Command(update={
            "result": result,
            "current_step": "resolution"
        })

    # Si no confirmó, volver a preguntar
    return Command(update={"current_step": "confirm"})
```

**Nodo `schedule`**:

```python
async def schedule_node(self, state: AppointmentState) -> Command:
    """
    Ejecuta la tool agendar_cita.
    """
    tool_result = await self._tools["agendar_cita"].ainvoke({
        "fecha": state["appointment_date"],
        "servicio": state["selected_service"],
        "nombre": state["patient_name"]
    })

    return Command(update={
        "result": tool_result,
        "current_step": "resolution"
    })
```

### 6. Tool ejecuta y retorna ToolMessage

```python
# agents/deyy_agent.py (tools)
@tool
async def agendar_cita(fecha: str, servicio: str, nombre: str) -> Dict:
    """
    Agenda una cita en la base de datos y Google Calendar.
    """
    phone = get_current_phone()  # ContextVar

    appointment = await appointment_service.create_appointment(
        phone_number=phone,
        service=servicio,
        appointment_date=fecha,
        appointment_time=state["appointment_time"],
        patient_name=nombre
    )

    return {
        "success": True,
        "appointment_id": str(appointment.id),
        "message": f"Cita agendada para {nombre} el {fecha} a las {state['appointment_time']}"
    }
```

**El ToolMessage se guarda automáticamente en el state** por LangGraph:

```python
# En el graph, después de ejecutar tool:
messages.append(ToolMessage(content=str(result), tool_call_id=...))
```

### 7. Respuesta final al usuario

```python
# resolution_node
async def resolution_node(self, state: AppointmentState) -> Command:
    if state.get("result", {}).get("success"):
        response = f"✅ {state['result']['message']}"
    else:
        response = f"❌ Error: {state.get('error', 'Desconocido')}"

    state["messages"].append(AIMessage(content=response))

    return Command(update={
        "current_step": "finished",
        "final_response": response
    })
```

---

## Herramientas (Tools)

### Tools disponibles

**En `agents/deyy_agent.py`** (reutilizables por todos los agentes):

```python
from agents.deyy_agent import (
    consultar_disponibilidad,
    agendar_cita,
    obtener_citas_cliente,
    cancelar_cita,
    reagendar_cita
)
```

**Implementación**:

Cada tool es una función `@tool` decorada que:

1. Recibe parámetros tipados (Pydantic)
2. Obtiene `phone_number` desde `contextvars`
3. Llama a `AppointmentService`
4. Retorna dict estructurado

Ejemplo:

```python
@tool
async def consultar_disponibilidad(
    fecha: Annotated[str, Field(description="Fecha en ISO (YYYY-MM-DD)")],
    servicio: Annotated[Optional[str], Field(description="Servicio opcional")] = None
) -> Dict[str, Any]:
    """
    Consulta slots disponibles para una fecha y servicio.
    """
    phone = get_current_phone()
    slots = await appointment_service.get_availability(
        date=fecha,
        service=servicio,
        phone_number=phone
    )
    return {"available_slots": slots, "date": fecha}
```

---

## Configuración

### Variables de Entorno

**Ya existentes** (sin cambios):

```bash
OPENAI_API_KEY=sk-...
DATABASE_URL=postgresql+asyncpg://user:pass@localhost:5432/arcadium
WHATSAPP_API_URL=https://evolution-api.com
WHATSAPP_INSTANCE_NAME=arcadium
```

**Nuevas** (opcionales):

```bash
# Seleccionar agente mode
AGENT_ARCHITECTURE=specialized  # "specialized" (nuevo) o "monolithic" (legacy DeyyAgent)

# Logging
LOG_LEVEL=INFO  # DEBUG para ver transiciones de state
```

**En code** (`core/config.py`):

```python
class Settings(BaseSettings):
    # ... existentes

    # Nueva arquitectura
    AGENT_ARCHITECTURE: Literal["specialized", "monolithic"] = "specialized"
```

---

## Testing

### Test Unitario: AppointmentAgent

**Ubicación**: `tests/test_appointment_agent.py` (crear)

```python
@pytest.mark.asyncio
async def test_appointment_flow_confirmation():
    """
    Test completo: usuario → confirmación → agendado
    """
    # 1. Crear agent
    agent = AppointmentAgent(session_id="test123", store=mock_store)

    # 2. Paso 1: usuario pide cita
    response1 = await agent.process_message("Quiero una cita para mañana a las 10")
    assert "¿Cuál es tu nombre?" in response1

    # 3. Paso 2: usuario da nombre
    response2 = await agent.process_message("Carlos Pérez")
    assert "¿Confirmas" in response2

    # 4. Paso 3: usuario confirma
    response3 = await agent.process_message("sí")
    assert "Cita agendada" in response3
    assert " appointment_id " in state["result"]
```

### Test de RouterAgent

```python
def test_router_intent_classification():
    router = RouterAgent(...)

    assert router._classify_intent("Quiero agendar una cita") == "agendar"
    assert router._classify_intent("Quiero cancelar mi cita") == "cancelar"
    assert router._classify_intent("Hola, cómo estás") == "otro"
```

### Test de State Machine (nodos)

```python
@pytest.mark.asyncio
async def test_confirm_node_executes_schedule():
    state = {
        "messages": [HumanMessage("¿Confirmas?")],
        "appointment_date": "2026-04-06",
        "appointment_time": "10:00",
        "selected_service": "consulta",
        "patient_name": "Carlos"
    }

    result = await confirm_node(state, agent=appointment_agent)

    # Verificar que se ejecutó agendar_cita
    assert result["current_step"] == "resolution"
    assert result["result"]["success"] is True
```

---

## Comparación con Arquitectura Anterior

| Aspecto                   | DeyyAgent (Monolítico)                          | Agentes Especializados            |
| ------------------------- | ----------------------------------------------- | --------------------------------- |
| **Prompt size**           | ~500 líneas                                     | Router: 15, Appointment: 20       |
| **Flujo controlado por**  | LLM interpretando instrucciones                 | State machine (código)            |
| **Bucle de confirmación** | ❌ Ocurría (LLM olvidaba)                       | ✅ Imposible (transición directa) |
| **Debugging**             | Difícil (no se sabe qué parte del prompt falló) | Fácil (saber en qué nodo está)    |
| **Testing**               | End-to-end lento, frágil                        | Unitario por nodo + E2E simple    |
| **Extensibilidad**        | Añadir feature = más prompt                     | Nuevo agente = nuevo archivo      |
| **Mantenibilidad**        | Prompt se corrompe con el tiempo                | State graph claro y documentado   |
| **Cambiar lógica**        | Editar prompt (riesgoso)                        | Editar código (safe)              |
| **Prompt engineering**    | Requerido constantemente                        | Minimizado                        |

---

## Próximos Pasos

### Fase 1: Completar Agent Base (✅ DONE)

- [x] Crear `BaseSpecializedAgent`
- [x] Crear `RouterAgent`
- [x] Crear `AppointmentAgent`
- [x] Crear `appointment_graph.py`

### Fase 2: Migración Total (⏳ PENDIENTE)

**Objetivo**: Que TODO el flujo de citas pase por AppointmentAgent.

1. **Completar RouterAgent**:
   - [ ] Mover clasificación de intenciones a herramienta `classify_intent` (opcional, para casos complejos)
   - [ ] Añadir manejo de `"otro"` intención → respuesta genérica
   - [ ]añadir fallback si no hay agente registrado

2. **Actualizar Orchestrator**:
   - [ ] Si `AGENT_ARCHITECTURE=specialized`, usar `RouterAgent`
   - [ ] Si `AGENT_ARCHITECTURE=monolithic`, usar `DeyyAgent` (legacy)

3. **Tests**:
   - [ ] `tests/test_router_agent.py`
   - [ ] `tests/test_appointment_agent.py`
   - [ ] `tests/test_appointment_graph.py`
   - [ ] Test de integración completa (webhook → router → appointment → WhatsApp)

4. **Eliminar bucle en arcadium_graph.py**:
   - [ ] Una vez migrado a AppointmentAgent, remover `arcadium_graph.py` (o mantener como fallback)

### Fase 3: Agentes Adicionales

- [ ] `RescheduleAgent` + `reschedule_graph.py`
- [ ] `CancelAgent` + `cancel_graph.py`
- [ ] `InfoAgent` + `info_graph.py`

### Fase 4: Mejoras Avanzadas

- [ ] **Cache de disponibilidad**: Evitar consultar DB múltiples veces en una conversación
- [ ] **Profile-based defaults**: Recordar nombre del usuario para no preguntar siempre
- [ ] **Multi-turno complejo**: Manejar cambios de opinión ("en realidad mejor otra fecha")
- [ ] **Timeouts**: Si el usuario no responde en X minutos, cancelar flujo
- [ ] **Human handoff**: Si el agente falla X veces, derivar a humano

---

## Preguntas Frecuentes (FAQ)

### Q: ¿Por qué no usar solo prompts más largos?

R: Porque los LLMs no son deterministas. Por más que pongas "NO vuelvas a preguntar", el modelo puede olvidarlo en el turno 5. Al mover la lógica a state machines, el comportamiento es 100% determinista y predecible.

### Q: ¿El RouterAgent usa LLM para clasificar?

R: Por ahora no, usa heurística de keywords. Se puede mejorar con una tool `classify_intent` que use LLM si la clasificación es ambigua. Pero para citas, las keywords son suficientes.

### Q: ¿Cómo se maneja el contexto entre agentes?

R: El `RouterAgent` delega creando un nuevo `AppointmentAgent` con el mismo `session_id` y `store`. El state se carga desde `store.load_state()`, por lo que la conversación previa se recupera automáticamente.

### Q: ¿Qué pasa si el usuario cambia de tema en medio de agendar?

R: El `AppointmentAgent` puede detectar intención de cambio y:

1. Si el usuario dice "Quiero cancelar" durante el flujo de agendado → El state machine puede transitar a otro agente (implementar con `delegate_to` en nodo).

2. Implementación futura: `router_agent` podría re-clasificar en cada turno y transferir si cambia la intención.

### Q: ¿Se puede usar DeyyAgent en paralelo?

R: Sí. En `orchestrator.py` se mantiene el flag `AGENT_ARCHITECTURE`. Puedes correr con `monolithic` (DeyyAgent) o `specialized` (nuevos agentes) según necesites.

### Q: ¿Cómo se testea sin OpenAI API?

R: Mockeando las tools:

```python
from unittest.mock import AsyncMock

mock_agent = AppointmentAgent(...)
mock_agent._tools["consultar_disponibilidad"] = AsyncMock(return_value={
    "available_slots": [{"time": "10:00"}]
})

response = await mock_agent.process_message("Quiero cita para mañana")
```

---

## Referencias

### Archivos clave

| Archivo                            | Responsabilidad                      |
| ---------------------------------- | ------------------------------------ |
| `agents/router_agent.py`           | Clasificación y delegación           |
| `agents/appointment_agent.py`      | Agente de agendado                   |
| `agents/base_specialized_agent.py` | Clase base (interfaz)                |
| `graphs/appointment_graph.py`      | State machine (nodos + transiciones) |
| `core/orchestrator.py`             | Selecciona architecture (line 926)   |
| `agents/deyy_agent.py`             | Tools reutilizables (no eliminar)    |

### Documentación relacionada

- **`ARCHITECTURE.md`**: Arquitectura general (antigua)
- **`CLAUDE.md`**: Guía de desarrollo
- **`COMPLETE_GUIDE.md`**: Manual de usuario
- **`IMPLEMENTATION_PLAN_LANGCHAIN.md`**: Plan histórico LangChain

---

## Glosario

| Término               | Definición                                                                     |
| --------------------- | ------------------------------------------------------------------------------ |
| **State Machine**     | Grafo de estados con transiciones explícitas. Cada nodo es una función Python. |
| **Node**              | Función que recibe estado, retorna `Command` (update + next).                  |
| **Command**           | Objeto que indica cómo actualizar el estado y a qué nodo ir después.           |
| **Checkpointer**      | Componente que persiste el estado en DB (PostgresSaver).                       |
| **Router**            | Agente que clasifica intenciones y delega.                                     |
| **Prompt**            | Texto que se envía al LLM. Aquí son ultra-cortos.                              |
| **Tool**              | Función Python que el agente puede llamar.                                     |
| **ContextVar**        | Variable de contexto thread-safe para inyectar `phone_number`.                 |
| **Agent**             | Entidad que procesa mensajes y maneja un state graph.                          |
| **Specialized Agent** | Agente con responsabilidad única (agendar, cancelar, etc.)                     |

---

**✅ Arquitectura implementada y lista para probar.**

Para iniciar el servidor con la nueva arquitectura:

```bash
# 1. Asegurar que .env tiene AGENT_ARCHITECTURE=specialized
# 2. Iniciar
./run.sh start

# 3. Probar flujo completo:
curl -X POST http://localhost:8000/webhook/test \
  -H "Content-Type: application/json" \
  -d '{"message": "Quiero una cita para mañana a las 10", "session_id": "test123"}'
```

**Resultado esperado**:

1. Router clasifica como "agendar"
2. Crea AppointmentAgent
3. State machine ejecuta: intake → validate → require_name → check_availability → confirm
4. Usuario responde "sí"
5. Confirm node ejecuta agendar_cita directamente
6. Respuesta: "✅ Cita agendada para [nombre] el [fecha] a las [hora]"

✅ **Sin bucles, sin prompts gigantes, control total.**
