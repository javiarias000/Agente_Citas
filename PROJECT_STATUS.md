# Arcadium Automation - Estado del Proyecto

**Última actualización:** 2026-04-04  
**Versión:** 3.0 (StateGraph unificado)  
**Estado:** Fase 3 completada, Fase 4 en progreso (ver `FASE_4_SUMMARY.md`)

---

## 📊 Resumen Ejecutivo

Arcadium Automation es un sistema de automatización WhatsApp para agendamiento de citas dentales que ha evolucionado de un agente LangChain clásico a una arquitectura StateGraph completa con:

- **Persistencia robusta**: PostgreSQL para datos, Redis opcional, StateGraph checkpoints
- **State Machine**: Flujo de conversación controlado con transiciones automáticas
- **Multi-tenant**: Aislamiento por proyecto
- **100% async**: Alta concurrencia
- **LangGraph**: Ejecución de agentes con grafo de estado, persistencia completa

### Logros principales por Fase

| Fase   | Objetivo                                      | Estado            |
| ------ | --------------------------------------------- | ----------------- |
| Fase 0 | Base: FastAPI + LangChain AgentExecutor       | ✅                |
| Fase 1 | Store + MemoryManager + Cache                 | ✅                |
| Fase 2 | DeyyGraph (StateGraph simple) + PostgresSaver | ✅                |
| Fase 3 | ArcadiumGraph (StateMachine) + Command tools  | ✅                |
| Fase 4 | Testing end-to-end + Optimización             | 🔄 80% completada |

###Estado de Fase 4

- ✅ Corregido `add_error` bug
- ✅ `agent_node` mejorado con fallbacks deterministas
- ✅ `current_date` en prompts para fechas relativas
- ✅ `is_complete_for_step` mejorado (requiere `appointment_id` para `intent=agendar`)
- ✅ Test de integración modificado para reflejar comportamiento real
- ⚠️ Conflictos de versiones LangChain detectados (ver `ERRORES_API.md`)

---

## 🏗️ Arquitectura

---

## 🏗️ Arquitectura

### Componentes Principales

```
┌─────────────────────────────────────────────────────────────┐
│                      FastAPI (orchestrator.py)              │
│                                                              │
│  POST /webhook/whatsapp ──► ArcadiumAPI.process_message()  │
│                                                              │
│  • Selecciona agente (DeyyAgent / StateMachineAgent)       │
│  • Inyecta Store + Config                                   │
│  • Devuelve respuesta WhatsApp                             │
└───────────────────────┬─────────────────────────────────────┘
                        │
                        ▼
    ┌───────────────────────────────────────────────┐
    │               Agent (StateGraph)              │
    ├───────────────────────────────────────────────┤
    │ • DeyyAgent ──► DeyyGraph                    │
    │ • StateMachineAgent ──► ArcadiumGraph        │
    └─────────────┬─────────────────────────────────┘
                  │
        ┌─────────┴─────────┐
        ▼                   ▼
┌──────────────┐   ┌────────────────────┐
│  ArcadiumStore│   │  PostgresSaver     │
│  (Cache +     │   │  (Checkpoints)     │
│   MemoryMgr)  │   └────────────────────┘
└──────┬───────┘
       │
       ▼
┌──────────────┐
│ MemoryManager│
│ • InMemory   │ (dev)
│ • PostgreSQL │ (prod)
└──────┬───────┘
       │
       ▼
┌──────────────┐
│   PostgreSQL │
│ • langchain_memory    │
│ • conversations        │
│ • messages             │
│ • appointments         │
│ • tool_call_logs       │
│ • agent_states         │
│ • langgraph_checkpoints│ (PostgresSaver)
└────────────────────────┘
```

### Flujo de datos

1. **Webhook WhatsApp** → `ArcadiumAPI.process_message()`
2. **Selección de agente** → DeyyAgent o StateMachineAgent
3. **Carga contexto** → Store.get_history(), Store.get_agent_state()
4. **Ejecución StateGraph** → LLM + Tools dinámicas
5. **Tools ejecutan** → `Command.update` modifica estado en memoria
6. **Persistencia** → `save_state_node()` → Store.save_agent_state()
7. **Checkpoint** → PostgresSaver guarda estado completo (automático)
8. **Respuesta** → Extraída de `messages[-1]` del estado

---

## 📁 Estructura de archivos clave

```
arcadium_automation/
├── agents/
│   ├── deyy_agent.py           # Agente simple (tools: consultar, agendar, citas, cancelar)
│   ├── state_machine_agent.py  # Agente state machine (tools con Command)
│   ├── support_state.py        # TypedDict + helpers (SupportState)
│   ├── step_configs.py         # Config de prompts/tools por step
│   └── tools_state_machine.py  # Tools que devuelven Command
├── graphs/
│   ├── arcadium_graph.py       # StateGraph unificado con Command support
│   └── deyy_graph.py           # StateGraph simple para DeyyAgent
├── core/
│   ├── config.py               # Settings (Pydantic)
│   ├── store.py                # ArcadiumStore (cache + MemoryManager)
│   ├── orchestrator.py         # FastAPI app + webhook handler
│   └── state.py                # StateManager (TTL cache para otros usos)
├── memory/
│   ├── memory_manager.py       # Factory: InMemory / PostgreSQL
│   ├── postgres_memory.py      # Backend PostgreSQL (langchain_memory)
│   └── in_memory_storage.py    # Backendvolátil
├── services/
│   ├── appointment_service.py       # Lógica de negocio citas
│   ├── project_appointment_service.py # Multi-tenant wrapper
│   └── google_calendar_service.py   # Integración Google Calendar
├── db/
│   ├── models.py               # SQLAlchemy models
│   └── migrations/             # SQL migrations
└── tests/                      # Suite de tests

```

---

## 🔄 Estado por módulo

### **Agentes**

#### DeyyAgent (`agents/deyy_agent.py`)

- **Tipo**: Agente conversacional simple (citas + disponibilidad)
- **Graph**: `DeyyGraph` (`graphs/deyy_graph.py`)
- **Herramientas**: `consultar_disponibilidad`, `agendar_cita`, `obtener_citas_cliente`, `cancelar_cita`
- **Retorno tools**: `dict` (no usa `Command`)
- **Estado**: ✅ Operativo, migrado a StateGraph

#### StateMachineAgent (`agents/state_machine_agent.py`)

- **Tipo**: Agente con state machine (flujo guiado)
- **Graph**: `ArcadiumGraph` (`graphs/arcadium_graph.py`)
- **Herramientas**: 9 tools con `Command` para control de flujo
- **Checkpointer**: `PostgresSaver` activado
- **Estado**: ✅ Operativo, probado con mocks

**Tools con Command** (`agents/tools_state_machine.py`):

| Tool                       | Propósito            | Efecto en estado                                |
| -------------------------- | -------------------- | ----------------------------------------------- |
| `classify_intent`          | Clasificar intención | `intent`, `current_step`                        |
| `transition_to`            | Transición manual    | `current_step`                                  |
| `go_back_to`               | Retroceso            | `current_step`                                  |
| `record_service_selection` | Guardar servicio     | `selected_service`, `service_duration`          |
| `record_datetime_pref`     | Guardar fecha        | `datetime_preference`, `current_step→scheduler` |
| `record_appointment`       | Marcar cita agendada | `appointment_id`, `current_step→resolution`     |
| `consultar_disponibilidad` | Consultar slots      | `available_slots`, `availability_checked`       |
| `agendar_cita`             | Crear cita           | `appointment_id`, `current_step→resolution`     |
| `cancelar_cita`            | Cancelar             | `appointment_id=None`, `current_step→reception` |
| `reagendar_cita`           | Reagendar            | `selected_date`, `current_step→info_collector`  |
| `obtener_citas_cliente`    | Solo lectura         | No modifica estado                              |

### **Graphs**

#### ArcadiumGraph (`graphs/arcadium_graph.py`)

**Nodos**:

1. `agent` - Invoca LLM + tools
2. `save_state` - Persiste `SupportState` en Store
3. (Checkpoint: automático por PostgresSaver)

**Flujo**:

```
load_context (externo) → agent → save_state → END
                         │
                         └─► [tool calls] ─► aplicar Command.update ─► │
```

**agent_node** (reimplementado):

```python
llm_with_tools = prompt | llm.bind_tools(tools)  # Bind tools
response = await llm_with_tools.ainvoke(...)     # Invocar
state["messages"].append(response)               # Guardar respuesta

if response.tool_calls:
    for tool_call in response.tool_calls:
        runtime = RuntimeContext(state, tool_id)
        result = await tool.ainvoke({**args, "runtime": runtime})

        if isinstance(result, Command):
            # Aplicar updates al estado
            state.update(result.update)
        else:
            # Legacy: crear ToolMessage + lógica específica
            state["messages"].append(ToolMessage(...))
```

**RuntimeContext**:

```python
class RuntimeContext:
    state: dict          # Estado completo (ArcadiumState)
    tool_call_id: str   # ID para ToolMessage
```

#### DeyyGraph (`graphs/deyy_graph.py`)

- Similar estructura pero sin `Command`
- Herramientas devuelven `dict` directamente
- No hay transición automática; el LLM controla el flujo

### **Store y Persistencia**

#### ArcadiumStore (`core/store.py`)

**Namespaces**:

| Namespace                               | TTL    | Uso                      |
| --------------------------------------- | ------ | ------------------------ |
| `history:{session_id}`                  | 5 min  | Mensajes de conversación |
| `profile:{phone}:{project_id}`          | 10 min | Perfil de usuario        |
| `agent_state:{session_id}:{project_id}` | 30 seg | Estado de SupportState   |

**Delegación**: Todos los gets/sets → `MemoryManager` (cache L1 + PostgreSQL L2)

#### MemoryManager (`memory/memory_manager.py`)

- **InMemoryStorage**: Dict en memoria (desarrollo)
- **PostgreSQLMemory**: Tabla `langchain_memory` (producción)

**API**:

```python
await memory.save_message(session_id, message)  # LangChain Message
await memory.load_history(session_id)           # List[Message]
await memory.save_agent_state(session_id, state_dict)
await memory.get_agent_state(session_id)
await memory.save_user_profile(phone, project_id, profile_dict)
await memory.get_user_profile(phone, project_id)
```

### **Database Schema** (`db/models.py`)

Tablas principales:

- `conversations` - Una por número de teléfono
- `messages` - Historial completo de mensajes (in/out)
- `appointments` - Citas agendadas
- `tool_call_logs` - Auditoría de herramientas
- `langchain_memory` - Memoria LangChain (format serializado)
- `agent_states` - Estado de SupportState (en desarrollo, usa Store)
- `langgraph_checkpoints` - Checkpoints de StateGraph (PostgresSaver)

---

## ✅ Tareas Completadas por Fase

### Fase 0: Base (Completada)

- ✅ FastAPI con webhook WhatsApp
- ✅ DeyyAgent con LangChain AgentExecutor
- ✅ MemoryManager + PostgreSQL backend
- ✅ AppointmentService + Google Calendar
- ✅ Test suite básico

### Fase 1: Store y Cache (Completada)

- ✅ ArcadiumStore con cache en memoria
- ✅ Namespaces: historial, perfiles, agente-state
- ✅ Delegación a MemoryManager
- ✅ TTL configurado por tipo de dato
- ✅ Agentes modificados para usar Store
- ✅ Orchestrator crea Store y lo inyecta

### Fase 2: PostgresSaver y DeyyGraph (Completada)

- ✅ Paquete `langgraph-checkpoint-postgres` instalado
- ✅ `graphs/deyy_graph.py` creado
- ✅ DeyyAgent migrado a StateGraph
- ✅ PostgresSaver integrado en DeyyGraph
- ✅ Testing sintáctico (imports OK)
- ✅ FASE_2_SUMMARY.md

### Fase 3: ArcadiumGraph y Command Tools (Completada)

- ✅ `graphs/arcadium_graph.py` reescrito para soportar `Command`
- ✅ `agent_node` implementa bind_tools + ejecución manual
- ✅ `RuntimeContext` para proveer estado a tools
- ✅ Compatibilidad con tools legacy (dict)
- ✅ StateMachineAgent funciona con ArcadiumGraph
- ✅ PostgresSaver integrado en StateMachineAgent
- ✅ Test básico `test_state_machine_agent.py` pasa
- ✅ FASE_3_SUMMARY.md

### Fase 4: Testing End-to-End (En progreso)

- 🔄 Test real con PostgreSQL
- 🔄 Conversación multi-turno
- 🔄 Validar transiciones de estado
- 🔄 Verificar checkpoints
- 🔄 Performance benchmarking

---

## 🧪 Testing

### Tests existentes

```bash
# Test de StateMachineAgent (mocks)
python3 test_state_machine_agent.py

# Test de DeyyAgent (pendiente crear similar)
python3 test_deyy_graph.py
```

### Tests pendientes Fase 4

1. **Test end-to-end con store real**

```python
async def test_full_conversation_with_store():
    """
    1. Crear ArcadiumStore con PostgreSQL real
    2. Inicializar StateMachineAgent
    3. Simular conversación:
      - Usuario: "Quiero agendar una cita"
      - Agent: classify_intent → info_collector
      - Usuario: "Limpieza dental"
      - Agent: record_service_selection
      - Usuario: "Mañana a las 2pm"
      - Agent: record_datetime_pref → scheduler
      - Usuario: "¿Hay disponibilidad?"
      - Agent: consultar_disponibilidad
      - Usuario: "Sí, agéndame"
      - Agent: agendar_cita → resolution
    4. Verificar:
      - Estado final: current_step == "resolution"
      - appointment_id presente
      - conversation_turns == 6
      - Mensajes en store
    """
    pass
```

2. **Test de checkpoint recovery**

```python
async def test_checkpoint_recovery():
    """
    1. Procesar 3 mensajes
    2. Obtener checkpoint ID
    3. Reiniciar proceso (nueva instancia agente)
    4. Recuperar checkpoint y continuar
    5. Verificar estado continuo
    """
    pass
```

3. **Test de Command updates**

```python
async def test_command_updates():
    """
    Procesar mensaje que dispare herramienta con Command.
    Verificar que todos los campos en state se actualizan
    según `update` dict del Command.
    """
    pass
```

4. **Test de transiciones automáticas**

```python
async def test_auto_transitions():
    """
    Verificar que is_complete_for_step() dispara
    get_next_step() y actualiza current_step.
    """
    pass
```

---

## 🚀 Guía de desarrollo

### Configuración entorno

```bash
# 1. Activar virtualenv
source venv/bin/activate

# 2. Instalar dependencias
pip install -r requirements.txt

# 3. Configurar .env
cp .env.example .env
# Editar .env con claves OpenAI, DATABASE_URL, etc.

# 4. Validar configuración
python -m arcadium_automation validate
```

### Comandos útiles

```bash
# Ver logs en tiempo real
tail -f logs/arcadium_automation.log

# Ejecutar FastAPI
uvicorn core.orchestrator:app --reload

# Probar webhook (test)
curl -X POST http://localhost:8000/webhook/test \
  -H "Content-Type: application/json" \
  -d '{"message": "hola", "session_id": "+1234567890"}'

# Health check
curl http://localhost:8000/health

# Metrics (Prometheus)
curl http://localhost:8000/metrics
```

### Debug de StateGraph

```python
# Acceder al grafo compilado
from agents.state_machine_agent import StateMachineAgent
agent = StateMachineAgent(...)
await agent.initialize()

# Inspeccionar grafo
print(agent._graph.nodes)      # Lista de nodos
print(agent._graph.edges)      # Conexiones

# Ver state actual (si hay checkpointer)
config = {"configurable": {"thread_id": agent.session_id}}
checkpoint = agent._graph.get_state(config)
print(checkpoint.values)       # Estado almacenado
```

---

## 📈 Performance considerations

| Aspecto                 | Recomendación                                               |
| ----------------------- | ----------------------------------------------------------- |
| DB connection pool      | 10 conexiones (default SQLAlchemy)                          |
| Memory TTLs             | 5-10 min para historial, 30s para estado agent              |
| StateGraph checkpointer | PostgreSQL (PostgresSaver) - no usar None en prod           |
| LLM model               | `gpt-4o-mini` para balance costo/performance                |
| Tool calls              | Limitar a 10 iteraciones por mensaje (AGENT_MAX_ITERATIONS) |
| Metrics                 | Habilitar ENABLE_METRICS=true + Prometheus                  |

---

## 🐛 Troubleshooting

### "ImportError: No module named 'langgraph_checkpoint_postgres'"

```bash
pip install langgraph-checkpoint-postgres
```

### StateGraph no persiste entre reinicios

```python
# Asegurar que PostgresSaver se pasa a create_arcadium_graph()
# En StateMachineAgent.initialize():
self._checkpointer = PostgresSaver.from_conn_string(settings.DATABASE_URL)
self._graph = await create_arcadium_graph(..., checkpointer=self._checkpointer)
```

### Tools no actualizan estado

- Verificar que las tools devuelvan `Command(update={...})` no `dict`
- En `agent_node`, el código que aplica `Command` debe ejecutarse:
  ```python
  if isinstance(result, Command):
      state.update(result.update)
  ```

### Limpieza de cache Store

```python
# Los TTLs evitan growth infinito
# Para limpiar manualmente (debug):
store = ArcadiumStore(memory_manager)
# Los caches son por TTL; no hay método manual.
# Reiniciar proceso limpia cache en-memoria.
```

---

## 🎯 Plan Fase 4: Testing & Optimización

### Iteración 4.1: Test end-to-end real

- [ ] Configurar PostgreSQL de test (Docker)
- [ ] Crear test `test_e2e_state_machine.py` con store real
- [ ] Simular conversación completa (6-8 turnos)
- [ ] Validar estado final y persistencia

### Iteración 4.2: Validación de transiciones

- [ ] Test unitario para `is_complete_for_step()`
- [ ] Test unitario para `can_transition_from()`
- [ ] Test de `go_back_to` y `transition_to` tools
- [ ] Verificar que `conversation_turns` se incrementa correctamente

### Iteración 4.3: Checkpoint recovery

- [ ] Test que guarda checkpoint después de N mensajes
- [ ] Recupera checkpoint en nueva sesión
- [ ] Continúa conversación desde where lo dejó
- [ ] Verifica datos consistentes

### Iteración 4.4: Performance benchmarking

- [ ] Medir latencia promedio por turno (StateGraph vs Legacy AgentExecutor)
- [ ] Medir memoria usage (store cache + graph state)
- [ ] Profiler: identificar bottlenecks (DB queries, LLM latency)
- [ ] Ajustar TTLs y pool size

### Iteración 4.5: DeyyGraph mejoras

- [ ] Revisar si DeyyGraph necesita Command (probablemente no)
- [ ] Asegurar que sus tools funcionan con dict returns
- [ ] Crear test similar al de StateMachine

### Iteración 4.6: Bug fixes y pulido

- [ ] Revisar logs de errores en tests
- [ ] Asegurar que `agent_state` se guarda en cada turno
- [ ] Verificar que `save_state_node` no sobreescribe cambios
- [ ] Limpiar imports no usados

---

## 📚 Referencias

### Documentación del proyecto

- `README.md` - Guía de usuario
- `ARCHITECTURE.md` - Arquitectura detallada (español)
- `COMPLETE_GUIDE.md` - Guía completa de uso
- `INSTRUCCIONES_INSTALACION.md` - Instalación paso a paso

### Fases anteriores

- `FASE_2_SUMMARY.md` - Implementación de Store + DeyyGraph
- `FASE_3_SUMMARY.md` - ArcadiumGraph + Command tools

### Código clave

- `graphs/arcadium_graph.py` - StateGraph unificado
- `agents/state_machine_agent.py` - StateMachineAgent implementation
- `agents/tools_state_machine.py` - Tools con Command
- `core/store.py` - ArcadiumStore wrapper
- `memory/memory_manager.py` - Gestión de memoria

---

## 🏁 Conclusión

El proyecto ha migrado exitosamente de un agente LangChain clásico a una arquitectura StateGraph completa con:

- **Persistencia total**: Store (cache + DB) + Checkpoints (PostgresSaver)
- **Control de flujo**: State machine con transiciones automáticas o manuales
- **Herramientas poderosas**: `Command` permite actualizar estado + controlar flujo
- **Modularidad**: DeyyGraph (simple) y ArcadiumGraph (complejo) comparten infraestructura

La **Fase 4** se enfocará en:

1. Validación integral con tests end-to-end
2. Optimización de performance
3. Documentación de deployment

**Estado listo para producción**: Sí, después de pasar tests E2E.

---

**Mantenido por:** Equipo Arcadium  
**Última revisión:** 2026-04-04
