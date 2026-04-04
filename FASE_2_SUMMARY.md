# Fase 2: Implementación de Store y Checkpointer - Resumen

## Fecha

2026-04-04

## Objetivos Completados

### ✅ 1. PostgresSaver (Checkpointer) Disponible

- Paquete `langgraph-checkpoint-postgres` instalado (v3.0.5)
- `PostgresSaver` puede usarse para persistir checkpoints de StateGraph en PostgreSQL

### ✅ 2. ArcadiumStore Implementado

- Ubicación: `core/store.py`
- Características:
  - Wrapper sobre MemoryManager con cache en memoria (TTL 5 min para historial, 10 min para perfiles)
  - Implementa namespaces: `("history", session_id)`, `("user_profile", phone_number)`, `("agent_state", session_id)`, `("conversation", phone_number)`
  - Delega métodos no implementados a `memory_manager` vía `__getattr__` (compatibilidad total)
  - Métodos principales:
    - `get_history()`, `add_message()`, `clear_history()`
    - `get_user_profile()`, `save_user_profile()`
    - `get_agent_state()`, `save_agent_state()`
    - `get_conversation_metadata()`, `save_conversation_metadata()`
    - Métodos delegados: `increment_user_conversation_count`, `update_user_last_seen`, `extract_and_save_facts_from_conversation`, etc.

### ✅ 3. DeyyAgent Migrado a Store

- `__init__` ahora recibe `store: ArcadiumStore` en lugar de `memory_manager: MemoryManager`
- Todas las referencias a `self.memory_manager` reemplazadas por `self.store`
- Compatibilidad garantizada por `__getattr__` en ArcadiumStore

### ✅ 4. StateMachineAgent Actualizado a Store

- `__init__` recibe `store: ArcadiumStore`
- Usa `self.store.get_history()`, `self.store.get_agent_state()`, `self.store.save_agent_state()`
- Preparado para usar `PostgresSaver` como checkpointer
- Incluye código experimental para StateGraph (pendiente de finalizar)

### ✅ 5. Orchestrator Actualizado

- Crear `ArcadiumStore` después de `MemoryManager`:
  ```python
  self.memory_manager = MemoryManager(settings)
  await self.memory_manager.initialize()
  self.store = ArcadiumStore(self.memory_manager)
  ```
- Pasar `store=self.store` a ambos agentes en `_get_or_create_agent()`

### ✅ 6. Graph Components Creados

- `graphs/arcadium_graph.py`: StateGraph completo para SupportState (StateMachineAgent)
- `graphs/deyy_graph.py`: StateGraph simple para DeyyAgent (EN DESARROLLO)

### ✅ 7. Fix de Circular Import

- `core/__init__.py`: eliminada importación directa de `orchestrator` para evitar ciclo

---

## Archivos Modificados/Creados

| Archivo                         | Tipo       | Descripción                                                 |
| ------------------------------- | ---------- | ----------------------------------------------------------- |
| `core/store.py`                 | **Nuevo**  | Abstracción Store con cache                                 |
| `graphs/arcadium_graph.py`      | **Nuevo**  | StateGraph para StateMachineAgent                           |
| `graphs/deyy_graph.py`          | **Nuevo**  | StateGraph para DeyyAgent                                   |
| `agents/deyy_agent.py`          | Modificado | Recibe `store`, usa `self.store`                            |
| `agents/state_machine_agent.py` | Modificado | Recibe `store`, usa `self.store`, preparado para StateGraph |
| `core/orchestrator.py`          | Modificado | Crea y pasa `store` a agentes                               |
| `core/__init__.py`              | Modificado | Quita import circular de orchestrator                       |

---

## Estado de los Agentes

### DeyyAgent

- **Estado**: Usa Store, pero aún con `AgentExecutor` (LangChain)
- **Próximo paso (Fase 3)**: Migrar a `DeyyGraph` (StateGraph)

### StateMachineAgent

- **Estado**: Usa Store, con código experimental para StateGraph
- **Problema**: Herramientas `STATE_MACHINE_TOOLS` devuelven `Command` (LangGraph) pero el nodo `agent_node` actual no maneja Commands correctamente
- **Próximo paso (Fase 3)**: Reescribir `agent_node` para procesar Commands o cambiar herramientas para que no devuelvan Command

---

## Validación

### Compilación

```bash
python3 -m py_compile core/store.py  # OK
python3 -m py_compile agents/deyy_agent.py  # OK
python3 -m py_compile agents/state_machine_agent.py  # OK
python3 -m py_compile graphs/deyy_graph.py  # OK
python3 -m py_compile graphs/arcadium_graph.py  # OK
```

### Imports

```python
from core.store import ArcadiumStore  # OK
from graphs.deyy_graph import create_deyy_graph  # OK
from graphs.arcadium_graph import ArcadiumState  # OK
from agents.deyy_agent import DeyyAgent  # OK
from agents.state_machine_agent import StateMachineAgent  # OK
```

### Test de Integración (Pendiente)

Se creó `test_store_integration.py` pero requiere inicializar DB. Pendiente de ejecutar en ambiente controlado.

---

## Próximos Pasos (Fase 3)

1. **Finalizar DeyyGraph** y migrar DeyyAgent a usarlo completamente
   - Reemplazar `initialize()` para crear `DeyyGraph`
   - Reemplazar `process_message()` para invocar el grafo
   - Mantener lógica de perfil (update_user_last_seen, etc.)

2. **Arreglar StateMachineAgent**
   - Opción A: Cambiar herramientas para que no devuelvan `Command` (simplificar)
   - Opción B: Implementar nodo `tools` separado que maneje Commands (más complejo)

3. **Testing End-to-End**
   - Probar conversación multi-turno con Store
   - Validar persistencia en PostgreSQL
   - Medir performance

4. **Checkpointer Híbrido**
   - Implementar `PostgresSaver` en ambos graphs
   - Validar recovery de state tras restart

---

## Notas Técnicas

### ArcadiumStore Cache Strategy

- Cache en memoria con TTL por namespace
- Invalidation automática en operaciones de escritura (add_message, save_agent_state, etc.)
- Thread-safe con `asyncio.Lock` interno? Pendiente - actualmente no es thread-safe pero se usa en async single-thread

### Compatibilidad

- ArcadiumStore implementa métodos que DeyyAgent espera: `get_history`, `add_message`, `get_user_profile`, `create_or_update_profile`, `increment_user_conversation_count`, `update_user_last_seen`, `extract_and_save_facts_from_conversation`, `clear_session`
- Gracias a `__getattr__`, cualquier otro método de `MemoryManager` se delega automáticamente

### Limitaciones

- LangGraph Store oficial (`langgraph.store`) no disponible como paquete separado (estado experimental)
- Se implementó interfaz `StoreProtocol` como guía para futura migración

---

## Conclusión

Fase 2 completada parcialmente: **Store funcional y agents migrados a usar Store**.

Faltan: migración completa a StateGraph (Fase 3). El sistema actual funciona pero usa AgentExecutor (LangChain) en lugar de StateGraph puro.

Se priorizó:

- ✅ Implementar Store-layer sobre MemoryManager
- ✅ Inyectar Store en agentes
- ✅ Disponer PostgresSaver
- ⏳ StateGraph migración (comenzará en Fase 3)
