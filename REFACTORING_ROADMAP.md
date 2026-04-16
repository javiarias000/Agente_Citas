# Refactoring Roadmap

## PHASE 2: Code Consolidation

### Graph Architecture
- **Canonical:** `src/graph_v2.py` (ReAct, 5 nodes)
- **Legacy:** `src/graph.py` (state machine, 20+ nodes) — kept as fallback
- **Action:** Remove `USE_GRAPH_V2` flag, always use V2 after testing
- **Timeline:** After comprehensive V2 testing

### Appointment Services (PRIORITY)
- **Duplication found:**
  - `services/appointment_service.py` — main DB adapter
  - `services/project_appointment_service.py` — project-scoped variant
  - `utils/arcadium_tools.py` — inline tools with partial logic
- **Action:** Consolidate into `services/appointment_service.py` with project scoping
- **Status:** Medium — affects graph logic

### Calendar Services (MEDIUM)
- **Current:**
  - `src/calendar_service.py` — wrapper around GoogleCalendarService
  - `services/composio_calendar_service.py` — MCP-based variant
  - `services/google_calendar_service.py` — direct Google API
- **Action:** Define single interface, support both MCP + direct
- **Status:** Medium — affects scheduling

### Dead Code (EASY)
- [ ] Remove `src/graph.py` after V2 stabilizes
- [ ] Remove `src/studio_graph.py` (unused)
- [ ] Remove `src/graph_v2.py.save` and similar backups
- [ ] Clean up `.py.save`, `_legacy`, `_old` files

### Config Cleanup
- [ ] Remove `USE_GRAPH_V2` — always use V2
- [ ] Remove `ENABLE_STATE_MACHINE` — always use LangGraph
- [ ] Validate that required env vars are present at startup
