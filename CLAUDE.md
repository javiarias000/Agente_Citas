# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

---

## Project Overview

**Arcadium Automation** is a 100% effective WhatsApp automation system built with Python, FastAPI, LangChain, and PostgreSQL. It replaces n8n workflows with direct API integrations and uses a custom "Landchain" architecture for guaranteed sequential processing with validation at each step.

**Key Features:**

- Landchain Architecture: Sequential processing chains with validation per link
- DeyyAgent: LangChain-based AI agent with tools for appointment management
- Memory Management: Pluggable backends (InMemory for dev, PostgreSQL for prod)
- Evolution API Integration: Direct WhatsApp messaging
- PostgreSQL Persistence: Conversations, messages, appointments, audit logs
- Prometheus Metrics: Built-in monitoring
- 100% async/await: High-performance asynchronous design

---

## Architecture

### High-Level Flow

```
WhatsApp → FastAPI Webhook → DeyyAgent → PostgreSQL + Tools → WhatsApp Response
```

### Core Components

**1. FastAPI Orchestrator** (`core/orchestrator.py`)

- `ArcadiumAPI` class: Main application container
- Handles webhooks, DB lifecycle, memory manager, WhatsApp service
- Endpoints:
  - `POST /webhook/whatsapp` - Main webhook
  - `POST /webhook/test` - Test without sending
  - `GET /health` - Health check
  - `GET /metrics` - Prometheus metrics
  - `GET /debug/agent/{session_id}` - Debug (DEBUG=true only)

**2. Landchain System** (`core/landchain.py`)

- `LandChain`: Sequential chain executor
- `ChainLink`: Individual processing step with validation, retries, rollback, timeout
- Guarantees execution with exponential backoff retry (1s, 2s, 4s)
- Built-in metrics per link

**3. DeyyAgent** (`agents/deyy_agent.py`)

- LangChain agent with `create_openai_tools_agent`
- Tools: `agendar_cita`, `consultar_disponibilidad`, `obtener_citas_cliente`, `cancelar_cita`
- Thread-safe phone_number injection via `contextvars`
- Memory managed by MemoryManager

**4. Memory Management** (`memory/memory_manager.py`)

- `MemoryManager`: Factory that selects backend based on config
- `InMemoryStorage`: Development (volatile)
- `PostgreSQLMemory`: Production using `langchain_memory` table
- Compatible with LangChain `ChatMessageHistory`

**5. State Management** (`core/state.py`)

- `StateManager`: Cached state storage with TTL
- Backends: `MemoryStorage`, `RedisStorage`, `SQLiteStorage`
- `StateKeys`: Helper for common keys (conversation, processing, etc.)

**6. Services**

- `WhatsAppService` (`services/whatsapp_service.py`): Evolution API client with retry
- `AppointmentService` (`services/appointment_service.py`): Business logic for appointments (availability checks, conflict detection, validation)

**7. Database** (`db/models.py`)

- `Conversation`: One per phone number
- `Message`: All inbound/outbound messages with agent responses
- `Appointment`: Scheduled appointments
- `ToolCallLog`: Audit trail for tool calls
- `LangchainMemory`: Conversation history for agents

**8. Chains** (`chains/`)

- `ArcadiumChainBuilder`: Builds processing chains (unified or processing)
- `DivisorChain`: LLM-based message splitting (category + priority)
- Integrates n8n workflow concepts into Python

---

## Common Development Commands

### Quick Start (New Terminal)

```bash
# 1. Activate virtualenv (required in each new terminal)
source venv/bin/activate

# 2. Run the system
./run.sh start

# 3. Test with demo
./run.sh demo

# 4. Check status
./run.sh status

# 5. View live logs
./run.sh logs

# 6. Run tests
./run.sh test --coverage
```

### Using `make`

```bash
make install      # Install dependencies
make test         # Run tests (pytest)
make test-cov     # Tests with coverage report
make lint         # Check code style (black + flake8)
make format       # Auto-format code
make run          # Start orchestrator
make demo         # Run quickstart demo
make validate     # Validate configuration
make check        # Quick health check
make logs         # Tail logs
make shell        # Interactive Python with orchestrator
```

### Using CLI Directly

```bash
python -m arcadium_automation start              # Start API server
python -m arcadium_automation process --file test_payload.json
python -m arcadium_automation status            # System status
python -m arcadium_automation metrics           # Live metrics
python -m arcadium_automation validate          # Validate config
python -m arcadium_automation test --coverage   # Run tests
```

---

## Configuration

### Environment Variables (`.env`)

**Required:**

- `OPENAI_API_KEY` - OpenAI API key
- `DATABASE_URL` - PostgreSQL connection (e.g., `postgresql+psycopg2://user:pass@localhost:5432/arcadium`)
- `WHATSAPP_API_URL` - Evolution API base URL
- `WHATSAPP_INSTANCE_NAME` - Evolution instance name

**Optional:**

- `WHATSAPP_API_TOKEN` - Evolution API token (if required)
- `USE_POSTGRES_FOR_MEMORY` - `true` (prod) or `false` (dev), default: `true`
- `SESSION_EXPIRY_HOURS` - Memory TTL, default: `24`
- `DEBUG` - `true` for debug mode, default: `false`
- `ENABLE_METRICS` - `true` to expose `/metrics`, default: `true`
- `METRICS_PORT` - Prometheus port, default: `9090`
- `OPENAI_MODEL` - e.g., `gpt-4o-mini`, default: `gpt-4o-mini`
- `OPENAI_TEMPERATURE` - 0.0-2.0, default: `0.7`
- `AGENT_MAX_ITERATIONS` - Agent loop limit, default: `10`
- `WEBHOOK_SECRET` - Optional webhook signature verification
- `LOG_LEVEL` - `DEBUG`, `INFO`, `WARNING`, `ERROR`, default: `INFO`

---

## Database

### Schema

5 main tables:

- `conversations` - One per phone number
- `messages` - All message history
- `appointments` - Appointments with status
- `tool_call_logs` - Audit trail
- `langchain_memory` - Agent conversation history (single table, scalable)

### Migrations

```bash
# Auto-run on app start (automatic)
./run.sh start

# Manual verification
python db/verify.py

# Manual schema creation
python db/create_schema_simple.py
```

### Useful Queries

```sql
-- Conversation history for a phone
SELECT * FROM messages
WHERE conversation_id = (
  SELECT id FROM conversations WHERE phone_number = '+1234567890'
)
ORDER BY created_at;

-- Appointment stats
SELECT * FROM appointment_stats WHERE phone_number = '+1234567890';

-- Recent activity
SELECT * FROM recent_activity LIMIT 20;
```

---

## Testing

### Run All Tests

```bash
./run.sh test --coverage
# OR
pytest tests/ -v --tb=short
```

### Test Structure

- `tests/test_n8n_client.py` - n8n integration (legacy)
- `tests/test_landchain.py` - LandChain execution
- `tests/test_state.py` - StateManager backends
- `tests/test_validators.py` - Pydantic schemas
- `tests/test_langchain_components.py` - LangChain integration
- `tests/test_tools.py` - Tool functions
- `tests/test_agent_deyy.py` - DeyyAgent
- `tests/test_divisor_chain.py` - DivisorChain
- `tests/test_integration.py` - End-to-end

### Testing Patterns

```python
import pytest
import asyncio

@pytest.mark.asyncio
async def test_something():
    result = await some_async_function()
    assert result.status == "success"
```

---

## Important Patterns

### LandChain Pattern

Each link has:

- Pre-validation via `validator` function
- Retry with exponential backoff
- Optional timeout
- Optional rollback function
- Can continue on failure (`continue_on_failure=True`)

Example from `chains/arcadium_chains.py`:

```python
chain.add_link(
    name="extract_and_validate",
    func=self._extract_and_validate,
    validator=self._validate_webhook_payload,
    max_retries=2,
    rollback_on_failure=True,
    rollback_func=self._rollback_extraction,
    metadata={"step": 1}
)
```

### ContextVar for Thread-Safe Data

`agents/deyy_agent.py` uses `contextvars` to inject `phone_number` into tools safely:

```python
_phone_context = contextvars.ContextVar('phone_number', default=None)

def set_current_phone(phone: str) -> contextvars.Token:
    return _phone_context.set(phone)

def get_current_phone() -> str:
    phone = _phone_context.get()
    if not phone:
        raise ValueError("No phone number set in context")
    return phone
```

Tools call `get_current_phone()` to access the current user's phone number without passing it explicitly.

### Memory Manager Selection

Based on `USE_POSTGRES_FOR_MEMORY`:

- `false` → `InMemoryStorage` (dev, ephemeral)
- `true` → `PostgreSQLMemory` (prod, persistent)

Both implement `BaseMemory` interface.

### State Keys

Use `StateKeys` helper for consistent key naming:

```python
from core.state import StateKeys

history_key = StateKeys.conversation(phone)  # f"conversation:{phone}"
processing_key = StateKeys.processing(conv_id)
transcription_key = StateKeys.transcription(phone)
```

---

## Debugging

### View Logs

```bash
./run.sh logs
# OR
tail -f logs/arcadium_automation.log
```

Set `LOG_LEVEL=DEBUG` in `.env` for verbose output.

### Debug Agent State

```bash
# With DEBUG=true, access:
curl http://localhost:8000/debug/agent/{session_id}

# Response includes:
{
  "session_id": "deyy_+1234567890",
  "initialized": true,
  "message_count": 15,
  "history": [...]
}
```

### Interactive Shell

```bash
./run.sh shell
# Opens Python with orchestrator pre-initialized
```

### Test Webhook Without Sending

```bash
curl -X POST http://localhost:8000/webhook/test \
  -H "Content-Type: application/json" \
  -d '{"message": "test", "session_id": "test123"}'
```

### Health Check

```bash
curl http://localhost:8000/health
# {"status":"healthy","timestamp":"...","version":"1.0.0"}
```

---

## Database Migrations

### Automatic

Migrations run automatically on app start (see `orchestrator.py::_run_migrations`).

### Manual

```bash
# Verify schema
python db/verify.py

# Create all tables (legacy method)
python db/create_schema_simple.py
```

### Migration Files

Location: `db/migrations/`

- `001_initial_schema.sql` - All tables, indexes, views, functions
- Run with Alembic or psql directly

---

## Dependencies

### Core Stack

**Async HTTP:** `aiohttp`, `httpx`
**Web Framework:** `fastapi`, `uvicorn`
**LLM:** `langchain`, `langchain-openai`, `langchain-community`
**Database:** `sqlalchemy` (2.0+), `psycopg2-binary`, `alembic`
**Validation:** `pydantic` (v2), `pydantic-settings`
**Logging:** `structlog`
**Monitoring:** `prometheus-client`
**Retry:** `tenacity`
**Config:** `python-dotenv`

### Optional

- `redis` - For RedisStorage (state) or cache
- `pgvector` - Vector embeddings (future)
- `websockets` - WebSocket support

**Full list:** `requirements.txt`

---

## File Structure

```
arcadium_automation/
├── agents/
│   ├── __init__.py
│   ├── deyy_agent.py          # Main DeyyAgent with tools
│   └── arcadium_agent.py      # Legacy ArcadiumAgent
├── chains/
│   ├── arcadium_chains.py     # Chain builder + link implementations
│   └── divisor_chain.py       # Message splitting LLM chain
├── config/
│   └── (config files, TBD)
├── core/
│   ├── config.py              # Pydantic Settings
│   ├── landchain.py           # LandChain system
│   ├── orchestrator.py        # FastAPI app (ArcadiumAPI)
│   ├── state.py               # StateManager + backends
│   └── exceptions.py          # Custom exceptions
├── db/
│   ├── models.py              # SQLAlchemy models
│   ├── migrate.py             # Migration runner
│   ├── create_schema_simple.py
│   ├── verify.py
│   └── migrations/            # SQL migration files
├── memory/
│   ├── memory_manager.py      # MemoryManager factory
│   ├── postgres_memory.py     # PostgreSQL backend
│   └── __init__.py
├── services/
│   ├── whatsapp_service.py    # Evolution API client
│   └── appointment_service.py # Appointment business logic
├── tools/
│   └── (utility tools)
├── utils/
│   ├── logger.py              # Logging setup
│   ├── transcriber.py         # Whisper transcription
│   ├── n8n_client.py          # Legacy n8n client
│   ├── langchain_components.py
│   └── monitor.py             # Metrics
├── validators/
│   └── schemas.py             # Pydantic validators
├── tests/                     # Test suite
├── data/                      # Persistent data (if using SQLite)
├── logs/                      # Application logs
├── venv/                      # Virtual environment (gitignored)
├── .env                       # Configuration (gitignored)
├── .env.example               # Example config
├── cli.py                     # CLI entry point
├── main.py                    # Alternative entry point
├── quickstart.py              # Demo script
├── run.sh                     # Helper script (recommended)
├── Makefile                   # Common commands
├── requirements.txt           # Dependencies
├── Dockerfile                 # Container build
├── docker-compose.yml         # Local stack
├── README.md                  # User-facing docs
├── ARCHITECTURE.md            # Detailed architecture (Spanish)
├── INSTRUCCIONES_INSTALACION.md # Installation guide (Spanish)
└── COMPLETE_GUIDE.md          # Complete usage guide
```

---

## Important Notes

### Before Making Changes

1. **Check existing patterns** - This codebase uses:
   - Async/await everywhere (no blocking calls)
   - Pydantic v2 for validation
   - SQLAlchemy 2.0 async ORM
   - ContextVar for thread-safe injection
   - LandChain for multi-step processing
   - Structured logging (structlog)

2. **Read the tests** - They demonstrate intended usage

3. **Validate config** - Always run `./run.sh validate` after config changes

4. **Migrations** - Schema changes need SQL in `db/migrations/` + update `models.py`

### Common Pitfalls

- **Blocking calls**: Avoid time.sleep(), use asyncio.sleep(). Use async DB sessions.
- **Missing context**: Tools need `phone` in contextvar; wrap agent calls with `set_current_phone()`.
- **Memory leaks**: Agent instances stored in `ArcadiumAPI._agents`; consider cleanup for inactive sessions.
- **DB sessions**: Always use `async with get_async_session() as session:` pattern.
- **Timezones**: Use UTC everywhere. Convert to local only for display.

---

## Performance Considerations

- **Connection pooling**: DB pool size 10 by default, adjust for high load
- **Memory backend**: PostgreSQL recommended for production (InMemory loses state on restart)
- **Agent caching**: Agents cached per session_id in `ArcadiumAPI._agents`
- **Metrics**: Disable `ENABLE_METRICS=false` if not using Prometheus
- **Batch processing**: DivisorChain supports `process_batch()` for efficiency

---

## Adding New Features

### New Tool for DeyyAgent

1. Define in `agents/deyy_agent.py`:

```python
@tool
async def nueva_herramienta(param: str) -> Dict[str, Any]:
    """Tool description"""
    phone = get_current_phone()
    # Implementation
    return {"success": True, "data": ...}
```

2. Add to agent's tool list in `initialize()`:

```python
tools = [..., nueva_herramienta]
```

3. Add tests in `tests/test_tools.py`

4. Update agent system prompt to mention new tool

### New Chain Link

In `chains/arcadium_chains.py`:

```python
async def _my_new_link(self, data: Dict, context: Dict) -> Dict:
    """Process something"""
    # Modify data in-place or return new dict
    data['my_field'] = result
    return data

def _validate_my_link(self, data: Dict) -> None:
    """Optional validator"""
    if 'required_field' not in data:
        raise ValueError("Missing required_field")

# Add to chain builder:
chain.add_link(
    name="my_new_link",
    func=self._my_new_link,
    validator=self._validate_my_link,
    timeout=60.0,
    rollback_on_failure=True,
    rollback_func=self._rollback_my_link,
    continue_on_failure=False,
    metadata={"description": "What this does"}
)
```

### New Database Model

1. Add to `db/models.py`:

```python
class NewModel(Base):
    __tablename__ = "new_table"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    # ... fields
```

2. Add to migration file in `db/migrations/`

3. Import where needed:

```python
from db.models import NewModel
```

---

## Security

- **Never commit `.env`** - It's in `.gitignore`. Use `.env.example` as template.
- **API keys**: All external APIs use keys from `.env`, never hardcoded.
- **Input validation**: Pydantic schemas validate all webhook inputs.
- **SQL injection**: SQLAlchemy ORM prevents it; avoid raw SQL.
- **Logging**: Sensitive data (phone numbers, message content) logged; consider masking for PII.
- **Webhook verification**: Optional `WEBHOOK_SECRET` for signature validation (not yet implemented).

---

## Monitoring

### Endpoints (when running)

- `GET /health` - Health status
- `GET /metrics` - Prometheus metrics (if `ENABLE_METRICS=true`)
- `GET /debug/agent/{session_id}` - Agent state (if `DEBUG=true`)

### Metrics Include

- `arcadium_chains_executed_total` - Chain execution count by name
- `arcadium_chains_duration_seconds` - Chain duration histogram
- `arcadium_links_executed_total` - Per-link metrics
- `arcadium_system_cpu_percent` - System CPU
- `arcadium_system_memory_percent` - System memory
- `arcadium_active_agents` - Number of active agent sessions

### Grafana Dashboard

Import Prometheus metrics into Grafana. Recommended panels:

- Chain success rate (last 1h, 24h)
- Agent response time (p50, p95, p99)
- System resource usage
- Appointment volume by hour

---

## Contributing

1. Fork and create feature branch
2. Follow existing patterns (async, Pydantic, logging)
3. Add tests for new functionality
4. Run `make lint` and fix any issues
5. Ensure `make test` passes with coverage
6. Document changes in code and README if needed
7. Submit PR

---

## Troubleshooting

### "No module named '...'"

```bash
source venv/bin/activate  # Ensure venv is active
pip install -r requirements.txt
```

### Database connection failed

```bash
# Verify DATABASE_URL in .env
# Ensure PostgreSQL is running:
sudo systemctl status postgresql
# Or with Docker:
docker-compose up -d postgres
```

### Redis connection failed

```bash
# Check Redis is running
redis-cli ping
# Or with Docker:
docker-compose up -d redis
```

### "ModuleNotFoundError: No module named 'langchain_postgres'"

```bash
pip install langchain-postgres
```

### Port already in use

```bash
# Change PORT in .env or kill process:
lsof -ti:8000 | xargs kill -9
lsof -ti:9090 | xargs kill -9
```

### High memory usage

- Reduce `SESSION_EXPIRY_HOURS`
- Set `USE_POSTGRES_FOR_MEMORY=true` to offload to DB
- Restart service periodically (cron)

---

## References

- **README.md** - User-facing quick start
- **ARCHITECTURE.md** - Detailed architecture in Spanish
- **COMPLETE_GUIDE.md** - Comprehensive usage guide
- **INSTRUCCIONES_INSTALACION.md** - Installation instructions in Spanish
- **db/README.md** - Database schema and migrations
- **.claude/servers/n8n-mcp/** - MCP server for n8n integration (separate project)

---

**Last Updated:** 2026-04-02  
**Version:** 2.0 (LangChain-based, no n8n dependency)
