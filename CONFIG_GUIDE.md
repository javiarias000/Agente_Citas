# Configuration Guide

## Overview

Configuración centralizada en **una sola fuente de verdad**: `core/config.py` usando Pydantic Settings v2.

### File Structure

```
core/config.py          # Configuración centralizada (Settings class)
.env                    # Variables de entorno (no versionado)
.env.example            # Template de variables requeridas
pyproject.toml          # Configuración de herramientas (tests, linting)
mypy.ini                # Type checking
pytest.ini              # Testing
.coveragerc             # Coverage reporting
.pre-commit-config.yaml # Git hooks
core/startup.py         # Validación de startup
```

## Categories

### 1. Critical (Sin estos no inicia)

```env
OPENAI_API_KEY=sk-...
DATABASE_URL=postgresql+asyncpg://...
WHATSAPP_API_URL=https://...
```

Validación: `core/startup.py` → `validate_environment()`

### 2. Security (Requerido en prod)

```env
DEBUG=false
API_KEY=...         # Para endpoints /api/*
WEBHOOK_SECRET=...  # Para webhooks
```

Validación en config.py: `field_validator('WEBHOOK_SECRET')`

### 3. Optional Features

- `GOOGLE_CALENDAR_*` — Google Calendar integration
- `CHATWOOT_*` — Chatwoot integration
- `USE_MEMORY_AGENT` — Semantic memory
- `REDIS_URL` — Cache (si no se define, sin cache)

## Usage

### Development

```bash
cp .env.example .env
# Editar .env con valores locales
python -m uvicorn core.orchestrator:app --reload
# Startup checks se ejecutan automáticamente
```

### Production

```bash
# Asegurar que DEBUG=false
# Asegurar que API_KEY y WEBHOOK_SECRET están configurados
# Ejecutar migraciones
alembic upgrade head
# Iniciar
gunicorn -w 4 core.orchestrator:app
```

## Adding New Settings

1. Agregar a `Settings` class en `core/config.py`:
```python
NEW_SETTING: str = Field(default="value", description="...")
```

2. Si es crítico, agregar validador:
```python
@field_validator('NEW_SETTING')
@classmethod
def validate_new_setting(cls, v: str) -> str:
    if not v:
        raise ValueError('NEW_SETTING is required')
    return v
```

3. Agregar a `.env.example`

4. Usar en código:
```python
from core.config import get_settings
settings = get_settings()
value = settings.NEW_SETTING
```

## Validation Flow

```
App Start
    ↓
lifespan context → run_startup_checks()
    ├─ validate_environment()     — Critical vars
    ├─ validate_database()        — Connection + tables
    ├─ validate_external_services()  — API keys format
    └─ validate_migrations()      — DB schema version
    ↓
initialize()
    ├─ Database setup
    ├─ Calendar services
    ├─ LLM initialization
    └─ Event loop setup
    ↓
✅ Server ready
```

## Common Issues

### Missing OPENAI_API_KEY
```
❌ CRÍTICA: OPENAI_API_KEY no configurado
```
**Fix:** Agregar a `.env`

### WEBHOOK_SECRET required in prod
```
⚠️  PRODUCCIÓN: WEBHOOK_SECRET requerido en DEBUG=false
```
**Fix:** Set DEBUG=true para dev, o agregar WEBHOOK_SECRET

### Database connection failed
```
Error BD: could not connect to server
```
**Fix:** Verificar DATABASE_URL, asegurar que PostgreSQL está corriendo

### Migrations not applied
```
Migraciones no inicializadas. Ejecuta: alembic upgrade head
```
**Fix:** 
```bash
alembic upgrade head
```

## Performance Tips

- Settings se cachea globalmente (`_settings` singleton)
- Use `get_settings()` en lugar de instanciar nuevamente
- Pydantic v2 valida una sola vez al startup, no en cada request

## Security Checklist

- [ ] API_KEY set in production
- [ ] WEBHOOK_SECRET set in production
- [ ] DEBUG=false in production
- [ ] CORS_ORIGINS restricted (no `["*"]` in prod)
- [ ] OPENAI_API_KEY never logged
- [ ] Database credentials not in code (use .env)
- [ ] SSL enabled for external APIs
