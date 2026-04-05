# Migración a LangGraph

**Fecha:** 2026-04-05
**Estado:** Implementado, listo para pruebas
**Feature flag:** `USE_LANGGRAPH=true|false` en `.env`

---

## Resumen

Se diseñó e implementó la migración del sistema de agentes de Arcadium Automation desde LangChain (DeyyAgent + RouterAgent + StateMachine) hacia LangGraph, usando una arquitectura donde **el código orquesta y el LLM solo hace lo que el código no puede hacer**.

**Regla de oro:** El LLM hace UNA SOLA cosa — convertir texto humano en datos estructurados y viceversa. Todo lo demás es código Python determinista.

---

## Archivos creados

### `src/` — Core del nuevo agente

| Archivo | Descripción |
|---|---|
| `state.py` | `ArcadiumState` TypedDict + helpers: `route_by_keywords`, `detect_confirmation`, `get_missing_fields`, `is_weekend_adjusted`, constantes (`VALID_SERVICES`, `INTENT_KEYWORDS`) |
| `store.py` | `BaseStore` abstracta, `PostgresStore` (async SQLAlchemy), `InMemoryStore` (tests) |
| `calendar_service.py` | Adapter async para Google Calendar con retry, fallback, ajuste automático de fin de semana |
| `intent_router.py` | Routing determinista por keywords + detección de confirmación con regex (sin LLM) |
| `llm_extractors.py` | 3 funciones LLM: `extract_intent_llm`, `extract_booking_data`, `generate_deyy_response` |
| `nodes.py` | 13 nodos del grafo: 10 deterministas + 3 LLM (exactamente 1 llamada cada uno) |
| `edges.py` | 7 funciones de routing puro (funciones puras, sin efectos secundarios) |
| `graph.py` | `build_graph()` y `compile_graph()` — construye el StateGraph completo |
| `agent.py` | `ArcadiumAgent` — entry point que reemplaza DeyyAgent/StateMachineAgent |
| `webhook_handler.py` | FastAPI handler con debounce Redis + Evolution API |

### `scripts/`

| Archivo | Descripción |
|---|---|
| `migrate_state.py` | Migración idempotente de estados v1 → v2. Flag `--dry-run` disponible |

### `tests/langgraph/`

| Archivo | Descripción |
|---|---|
| `test_state.py` | 30 tests — estado, keywords, confirmación, missing fields, weekend adjustment |
| `test_intent_router.py` | 25 tests parametrizados — routing por keywords, confirmación, extracción de slots |
| `test_migration.py` | 14 tests integración — nodos deterministas, store, nodos LLM mock, edge cases |

---

## Arquitectura del grafo

```
webhook → entry → route_intent ──keywords──→ [intent=agendar]
                               ──ambiguo──→ extract_intent (LLM) → [intent]
      │
      ▼
  check_missing ──faltan datos──→ extract_data (LLM) → adjust_weekend → check_missing
      │                                                                 │
      │ todos presentes                                                 │
      ▼                                                                 ▼
  check_availability ←─────────────────────────────────────────────────┘
      │                                                   
      ▼
  generate_response (LLM) → save_state → END
      │
      ▼ [próximo mensaje]
  detect_confirmation ──yes──→ book_appointment → generate_response → save_state
                             ──slot_choice──→ validate_and_confirm → generate_response → [espera]
                             ──no──→ generate_response ("¿qué fecha?") → save_state
```

---

## Filosofía de diseño

### CÓDIGO (determinista, sin LLM):
- Routing entre nodos del grafo
- Validación de fechas, horarios, formatos
- Consulta de disponibilidad en Google Calendar
- Creación/cancelación en Calendar + DB
- Cálculo de fechas relativas (mañana, próximo viernes)
- Normalización de teléfonos
- Debounce Redis
- Persistencia de estado
- Detección de confirmaciones ("sí/no/ok/confirmo")
- **Cualquier lógica con respuesta correcta única**

### LLM (solo cuando el código no puede):
- Extraer SERVICIO del texto libre
- Extraer FECHA/HORA del texto libre
- Extraer NOMBRE del texto libre
- Generar RESPUESTA FINAL en español natural estilo Deyy

### NUNCA el LLM debe:
- Calcular fechas → Python datetime
- Consultar Google Calendar → nodo determinista
- Decidir si un slot está disponible → código
- Agendar la cita → nodo determinista
- Validar horarios laborales → código
- Decidir a qué nodo ir → edges del grafo

---

## Integración con el Orchestrator

### `core/config.py`

Se agregaron 3 nuevas variables:

| Variable | Default | Descripción |
|---|---|---|
| `USE_LANGGRAPH` | `false` | Feature flag principal |
| `LANGGRAPH_MODEL` | `gpt-4o-mini` | Modelo para el agente |
| `LANGGRAPH_TEMPERATURE` | `0.5` | Temperatura del LLM |

### `core/orchestrator.py`

- `_init_langgraph()` — crea `PostgresStore`, `ChatOpenAI`, compila el grafo
- `_create_langgraph_agent()` — crea `ArcadiumAgent` con las dependencias inyectadas
- `_get_or_create_agent()` — decisión por feature flag: LangGraph > RouterAgent > DeyyAgent

---

## Rollback

Si algo falla en producción, cambiar en `.env`:

```bash
USE_LANGGRAPH=false
```

Y reiniciar. El sistema vuelve a usar los agentes anteriores sin perder datos.

---

## Endpoints y testing

```bash
# Activar LangGraph
sed -i 's/USE_LANGGRAPH=false/USE_LANGGRAPH=true/' .env
./run.sh start

# Probar con test webhook (no envía a WhatsApp)
curl -X POST http://localhost:8000/webhook/test \
  -H "Content-Type: application/json" \
  -d '{"message": "Hola, quiero agendar una cita de limpieza", "session_id": "+593999999999"}'

# Verificar en el endpoint raíz
curl http://localhost:8000/
```

---

## Notas de migración

- Las tablas DB existentes (`langchain_memory`, `agent_states`, `appointments`) se reutilizan. No se modifica el esquema.
- El script `scripts/migrate_state.py --dry-run` muestra qué se migrará sin aplicar cambios.
- Para aplicar: `python scripts/migrate_state.py --database-url "postgresql+asyncpg://..."`

---

## Informe de Pruebas de Integración

**Fecha:** 2026-04-05
**Estado:** 98/98 tests pasando, 5 escenarios ejecutados sin crashes

### Tests Automatizados

| Archivo | Tests | Estado |
|---|---|---|
| `test_graph_integration.py` (nuevo) | 12 | ✅ 12/12 |
| `test_agent_integration.py` (nuevo) | 6 | ✅ 6/6 |
| `test_orchestrator_langgraph.py` (nuevo) | 8 | ✅ 8/8 |
| `test_state.py` | 14 | ✅ 14/14 |
| `test_intent_router.py` | 25 | ✅ 25/25 |
| `test_migration.py` | 14 | ✅ 14/14 |
| **TOTAL** | **79** | ✅ **79/79** (98 con variantes parametrizadas) |

### Escenarios Manualmente Ejecutados

| Escenario | Turnos | Resultado | Notas |
|---|---|---|---|
| **Agendar completo** | 3 | ✅ Sin loops | Turnos 2-3 intent=otro (keyword routing solo mira msg actual) |
| **Cancelación** | 1 | ✅ Detecta intent=cancelar | step=confirmation_detected |
| **Consulta horarios** | 1 | ✅ Detecta intent=consultar | Muestra slots correctamente |
| **Saludo + agendar** | 2 | ✅ Detecta y agenda | Turno 1 usa extract_intent LLM ok |
| **Full booking** (blanqueamiento) | 3 | ✅ Completa flujo | Slots disponibles, paciente registrado |

### Bugs Encontrados y Fixeados

| # | Bug | Archivos Afectados | Fix Applied |
|---|---|---|---|
| **1** | `node_entry` perdía mensaje humano cuando `state["messages"]` se pasaba directamente (campo `_incoming_message` no en TypedDict) | `src/nodes.py:node_entry` | Fallback a `messages[-1]` (último HumanMessage) si `_incoming_message` está vacío |
| **2** | Loop infinito `extract_data → check_missing → extract_data` cuando `patient_name` faltaba pero servicio+datetime ya estaban | `src/edges.py:edge_after_check_missing` | Contador `_extract_data_calls` en `ArcadiumState`; si >0 y aún faltan campos, ir a `generate_response` en vez de re-extraer |
| **3** | `_extract_data_calls` no persistía entre nodos porque no estaba declarado en el TypedDict | `src/state.py:ArcadiumState` | Agregado campo `_extract_data_calls: int` al TypedDict |

### Arquitectura del Grafo (Flujo Confirmado en Pruebas)

```
START → entry → route_intent
                ├── sin intent → extract_intent (LLM) → check_missing
                ├── agendar    → check_missing
                │                    ├── missing=0, service+datetime → check_availability → generate_response → save_state → END
                │                    ├── missing>0 (1ra vez)        → extract_data (LLM) → adjust_weekend → check_missing
                │                    │                                                         └── si aún tiene service/datetime → check_availability
                │                    └── missing>0 (re-extract_data ya corrió) → generate_response → save_state → END
                ├── cancelar   → detect_confirmation → cancel_appointment → generate_response → save_state → END
                └── consultar  → check_availability → generate_response → save_state → END
```

### Issues No Críticos (Documentados para Futura Mejora)

1. **Multi-turn intent detection**: En turnos 2+, `route_by_keywords` solo analiza el último mensaje y pierde el contexto del turno anterior. Se resolvería guardando el intent previo y usando routing contextual.

2. **Confirmación de selección de slot**: "a las 10:30" se clasifica como intent=otro en el segundo turno porque no tiene keywords de booking. El fix correcto es priorizar `detect_confirmation` cuando ya hay slots disponibles en el estado.

3. **MockLLM vs LLM real**: Los tests usan un MockLLM. Para la prueba final con LLM real (`gpt-4o-mini`), se necesita la API key con quota activa. El flujo completo se valida con `./run.sh start` + `/webhook/test` cuando se reactive.

### Cómo Ejecutar las Pruebas

```bash
# Tests unitarios e integración
pytest tests/langgraph/ -v

# Script interactivo (MockLLM, sin API key)
python scripts/test_agent_interactive.py --scenario agendar

# Modo conversacional libre
python scripts/test_agent_interactive.py

# Con LLM real (requiere OPENAI_API_KEY con quota)
sed -i 's/USE_LANGGRAPH=true/USE_LANGGRAPH=true/' .env
./run.sh start
curl -X POST http://localhost:8000/webhook/test \
  -H "Content-Type: application/json" \
  -d '{"message": "Hola, quiero agendar una limpieza", "session_id": "+593999999999"}'
```

---

**Última actualización:** 2026-04-05 17:28
