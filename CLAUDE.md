# CLAUDE.md

## Stack
Python + FastAPI + LangGraph + PostgreSQL. WhatsApp via Evolution API. No n8n.

## Entry Points
- `src/agent.py` — ArcadiumAgent (main, LangGraph)
- `src/graph.py` — StateGraph builder/compiler
- `src/nodes.py` — nodos del grafo (async, 0 LLM los deterministas)
- `src/edges.py` — routing puro (lee estado, retorna string, nunca muta)
- `src/calendar_service.py` — adapter async sobre GoogleCalendarService
- `core/orchestrator.py` — FastAPI app, webhooks, lifecycle

## Run
```bash
source venv/bin/activate
./run.sh start      # servidor
./run.sh logs       # logs en vivo
./run.sh test       # pytest
```

## Key Patterns

**Nodos:** async, retornan solo los campos que modifican, capturan excepciones en `last_error`.

**Edges:** funciones puras, sin LLM, sin efectos secundarios.

**create_event:** retorna `tuple[str, str]` → `event_id, link = await svc.create_event(start=dt, end=end_dt, title=..., description=...)`.

**Guards en generate_response:** leer desde `state` (plano), NO desde `context_dict` (anidado). `context_dict = _build_llm_context(state)` tiene estructura `calendar.*, flow.*, availability.*`.

**confirmation_sent:** único indicador de que una operación fue ejecutada. `google_event_id` solo = cita encontrada, no necesariamente creada.

**awaiting_confirmation:** debe ser `True` para que slots en estado redirijan a `detect_confirmation`. Sin esto, slots rancios atrapan nuevas conversaciones.

## DB
```
conversations / messages / appointments / tool_call_logs / langchain_memory
```
Sesiones siempre con `async with get_async_session() as session:`.

## Errores conocidos / ya resueltos
Ver `errores_agenda.md` — 5 clases de bugs documentados con causa raíz y solución.

## Config (.env)
`OPENAI_API_KEY`, `DATABASE_URL`, `WHATSAPP_API_URL`, `WHATSAPP_INSTANCE_NAME` — requeridos.
`OPENAI_MODEL` (default: gpt-4o-mini), `LOG_LEVEL`, `DEBUG`, `USE_POSTGRES_FOR_MEMORY`.

## Pitfalls
- Sin `asyncio.sleep` bloqueante — usar `await asyncio.sleep()`
- Timezones: siempre UTC internamente, Ecuador (UTC-5) solo para display
- `cancel_appointment` en DB requiere session real, no `None`
- `get_available_slots(date=dt)` espera `datetime`, no `date`
