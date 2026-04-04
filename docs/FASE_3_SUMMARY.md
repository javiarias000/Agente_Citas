# Fase 3: Unificación de Agentes a StateGraph - Resumen

**Fecha:** 2026-04-04

## Objetivos Completados

### ✅ 1. StateMachineAgent migrado completamente a StateGraph

- **Problema resuelto**: Las herramientas (`tools_state_machine.py`) devuelven `Command` y requieren `runtime` para acceder al estado. El nodo `agent_node` original usaba `create_openai_tools_agent` (LangChain) que no soporta `Command` ni pasa `runtime`.
- **Solución**: Reescrito `agent_node` en `graphs/arcadium_graph.py` para:
  - Usar `llm.bind_tools()` en lugar de `create_openai_tools_agent`
  - Ejecutar tool calls manualmente, inyectando `RuntimeContext` (provee `state` y `tool_call_id`)
  - Aplicar `Command.update` al estado cuando las tools devuelven `Command`
  - Mantener compatibilidad con herramientas legacy que devuelven `dict`

### ✅ 2. ArcadiumGraph mejorado con soporte para Command

**Cambios en `graphs/arcadium_graph.py`:**

- Imports: añadidos `ToolMessage`, `Command`, `BaseTool`
- `agent_node` completamente reimplementado:
  - Bind tools al LLM: `prompt | llm.bind_tools(tools)`
  - Invocación directa con `ainvoke`
  - Bucle de ejecución de herramientas con `RuntimeContext`
  - Aplicación de updates desde `Command`
  - Lógica legacy para herramientas sin Command
  - Transición automática cuando no hay tool calls
- Se mantienen los nodos `load_conversation_context` y `save_state_node`
- `build_arcadium_graph` ya usaba wrappers async para capturar store/llm/tools

### ✅ 3. PostgresSaver integrado

- **StateMachineAgent**: crea `PostgresSaver` en `initialize()` y lo pasa a `create_arcadium_graph()`
- **DeyyGraph**: ya creaba `PostgresSaver` automáticamente si no se proporcionaba
- Persistencia de estado entre conversaciones habilitada

### ✅ 4. DeyyAgent ya migrado a StateGraph (Fase 2)

- `agents/deyy_agent.py` usa `DeyyGraph` implementado en `graphs/deyy_graph.py`
- DeyyGraph es más simple, no usa Command (sus tools devuelven dict)
- Sin cambios necesarios en esta fase

### ✅ 5. Testing básico

- Test `test_state_machine_agent.py`:
  - Verifica inicialización sin errores
  - Mock de store y grafo
  - `process_message` retorna respuesta correctamente
- Pruebas de compilación:
  - `graphs/arcadium_graph.py` compila OK
  - `agents/state_machine_agent.py` importa OK
  - `agents/deyy_agent.py` importa OK

---

## Detalles Técnicos

### RuntimeContext

Clase auxiliar creada dentro de `agent_node` para proveer a las herramientas acceso al estado en memoria (`state`) y al `tool_call_id` para crear `ToolMessage`.

```python
class RuntimeContext:
    def __init__(self, state_dict, call_id):
        self.state = state_dict
        self.tool_call_id = call_id
```

### Flujo de agent_node (StateMachineAgent)

1. Determinar `current_step`
2. Obtener `prompt` y `tools` para ese step (desde `step_configs`)
3. Bind tools al LLM: `llm_with_tools = prompt | llm.bind_tools(tools)`
4. Invocar LLM con historial → `AIMessage` (puede tener `tool_calls`)
5. Añadir AIMessage a `state["messages"]`
6. Si hay `tool_calls`:
   - Para cada tool call:
     - Buscar herramienta en lista
     - Crear `RuntimeContext(state, tool_id)`
     - Ejecutar `await tool.ainvoke({**args, "runtime": runtime})`
     - Si resultado es `Command`: aplicar `update` al state (incluye mensajes)
     - Si es dict: crear `ToolMessage` y aplicar lógica legacy (actualizar campos específicos)
7. Si no hay tool calls, verificar completitud del step y transitar automáticamente
8. Incrementar `conversation_turns`

### Compatibilidad

- Herramientas que devuelven `Command`: `classify_intent`, `transition_to`, `go_back_to`, `record_service_selection`, `record_datetime_pref`, `record_appointment`, `consultar_disponibilidad`, `agendar_cita`, `cancelar_cita`, `reagendar_cita` (todas en `tools_state_machine.py`)
- Herramientas legacy que devuelven `dict`: `obtener_citas_cliente` (y posiblemente otras en `deyy_agent.py`)

---

## Archivos Modificados/Creados

| Archivo                         | Cambios                                                      |
| ------------------------------- | ------------------------------------------------------------ |
| `graphs/arcadium_graph.py`      | ✅ Reescrito `agent_node` con soporte para Command y runtime |
| `agents/state_machine_agent.py` | ✅ Ya usaba ArcadiumGraph, no necesitaba cambios adicionales |
| `test_state_machine_agent.py`   | ✅ Test de verificación                                      |
| `graphs/deyy_graph.py`          | ✅ Creado previamente, sin cambios                           |
| `agents/deyy_agent.py`          | ✅ Migrado a StateGraph previamente                          |

---

## Estado de Tareas Fase 3

| ID    | Tarea                                               | Estado                             |
| ----- | --------------------------------------------------- | ---------------------------------- |
| F3-T1 | Migrar DeyyAgent a StateGraph                       | ✅ Completado (Fase 2)             |
| F3-T2 | Arreglar StateMachine tools (Command compatibility) | ✅ Completado                      |
| F3-T3 | Integrar PostgresSaver                              | ✅ Completado                      |
| F3-T4 | Migrar StateMachineAgent a StateGraph               | ✅ Completado                      |
| F3-T5 | Testing end-to-end                                  | 🟡 Parcial (test básico con mocks) |

---

## Próximos Pasos (Fase 4: Testing y Optimización)

1. **Testing end-to-end real**:
   - Test con store real (PostgreSQL o SQLite)
   - Simular conversación multi-turno con herramientas reales
   - Verificar que `Command` actualiza estado correctamente
   - Validar persistencia con PostgresSaver

2. **Validación de transiciones**:
   - Verificar que `current_step` cambia según herramientas
   - Confirmar que `conversation_turns` se incrementa
   - Probar `is_complete_for_step` y `get_next_step`

3. **Benchmarks de performance**:
   - Comparar latencia entre agente Legacy y StateGraph
   - Medir uso de memoria

4. **Corregir DeyyGraph si es necesario**:
   - Asegurar que DeyyGraph también funciona con sus herramientas actuales

5. **Documentación**:
   - Actualizar `ARCHITECTURE.md` con diagrama de StateGraph
   - Documentar el patrón `RuntimeContext` y `Command` en tools

---

## Notas

- La migración a StateGraph permite mayor control y trazabilidad del estado.
- El uso de `Command` en las herramientas es poderoso pero acopla las tools a LangGraph.
- Se mantiene compatibilidad hacia atrás para herramientas legacy.
- PostgresSaver habilita recovery de conversaciones tras reinicios.
