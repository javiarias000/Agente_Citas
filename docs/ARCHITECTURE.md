# Arquitectura Arcadium Automation (v2 - Sin n8n)

## 📋 Índice

1. [Visión General](#visión-general)
2. [Flujo de Datos](#flujo-de-datos)
3. [Componentes](#componentes)
4. [Buenas Prácticas Aplicadas](#buenas-prácticas-aplicadas)
5. [Despliegue](#despliegue)
6. [Configuración](#configuración)

---

## Visión General

Arquitectura limpia y desacoplada para automatización de WhatsApp sin dependencia de n8n.

```
WhatsApp → FastAPI Webhook → DeyyAgent → PostgreSQL + Tools → Respuesta WhatsApp
```

### Características

- **FastAPI**: API asincrónica de alto rendimiento
- **LangChain moderno**: Herramientas y agentes sin deprecated APIs
- **PostgreSQL**: Persistencia de datos y conversaciones
- **Memoria configurable**: InMemory (dev) o Postgres (prod)
- **Evolution API**: Integración directa con WhatsApp
- **ContextVars**: Inyección segura de estado en herramientas
- **Pydantic v2**: Validación de configuraciones y datos
- **Docker**: Contenedorización completa
- **Metricas**: Prometheus integrado

---

## Flujo de Datos

### 1. Recepción

```
Evolution API → POST /webhook/whatsapp
```

Payload esperado:

```json
{
  "sender": "1234567890",
  "message": "Hola, quiero agendar una cita",
  "message_type": "text"
}
```

### 2. Procesamiento

```
FastAPI Webhook Handler
    ↓
Parsear payload
    ↓
Obtener/crear Conversation (DB)
    ↓
Guardar Message inbound (DB)
    ↓
Obtener DeyyAgent (session_id = sender)
    ↓
Añadir mensaje a Memory (historial)
    ↓
Ejecutar AgentExecutor (LangChain)
    ↓
Agente elige herramienta (si es necesario)
    ↓
Tool llama a AppointmentService/DB
    ↓
Generar respuesta final
    ↓
Guardar mensaje AI en Memory
    ↓
Actualizar Message con respuesta
    ↓
Enviar respuesta a WhatsApp via WhatsAppService
    ↓
Commit DB transaction
```

### 3. Respuesta

```
WhatsApp API ← Mensaje de texto enviado al cliente
```

---

## Componentes

### 1. Core

- **`config.py`**: Settings con Pydantic v2, carga desde `.env`, validación estricta
- **`orchestrator.py`**: ArcadiumAPI (FastAPI app), lifespans, endpoints, dependency management

### 2. Agentes

- **`agents/deyy_agent.py`**:
  - DeyyAgent con LangChain `create_openai_tools_agent`
  - Tools: `agendar_cita`, `consultar_disponibilidad`, `obtener_citas_cliente`, `cancelar_cita`
  - Inyección de `phone_number` via `contextvars` (thread/async-safe)
  - Memoria gestionada por MemoryManager

### 3. Memoria

- **`memory/memory_manager.py`**:
  - `BaseMemory` (interface)
  - `InMemoryStorage` (desarrollo)
  - `PostgreSQLMemory` (producción con langchain-postgres)
  - `MemoryManager` (factory con selector automático)
  - Compatible con LangChain ChatMessageHistory

### 4. Servicios

- **`services/whatsapp_service.py`**:
  - WhatsAppMessage (value object)
  - WhatsAppService ( Evolution API client )
  - Retry automático con tenacity
  - Soporte texto, imágenes, botones

- **`services/appointment_service.py`**:
  - AppointmentService (negocio de citas)
  - Validación de horarios laborales
  - Chequeo de conflictos
  - Gestión completa de citas (CRUD)

### 5. Base de Datos

- **`db/models.py`**:
  - `Conversation`: Cabecera de conversación
  - `Message`: Mensajes (in/out) con tool calls
  - `Appointment`: Citas agendadas
  - `ToolCallLog`: Audit trail de herramientas

- **`db/__init__.py`**: Gestión de sesiones async, `get_async_session()`

### 6. FastAPI

- **Endpoints**:
  - `POST /webhook/whatsapp`: Webhook principal
  - `POST /webhook/test`: Test sin enviar a WhatsApp
  - `GET /health`: Health check
  - `GET /metrics`: Métricas Prometheus (si habilitado)
  - `GET /debug/agent/{session_id}`: Debug (solo DEBUG=true)

---

## Buenas Prácticas Aplicadas

### 1. Inyección de Dependencias

- Settings inyectados en constructores (no全局 variables)
- Sesiones DB pasadas como parámetros
- Servicios inyectados en agentes

```python
api = ArcadiumAPI(settings)  # inyectado
agent = DeyyAgent(session_id, memory_manager, whatsapp_service)
```

### 2. Separación de Responsabilidades

- **FastAPI**: Solo HTTP, delega a servicios
- **Agente**: Orquestación de LLM + tools
- **Tools**: Lógica de negocio específica
- **Services**: Comunicación externa (WhatsApp)
- **DB**: Persistencia (SQLAlchemy models)

### 3. Async/Await Completo

- FastAPI async endpoints
- SQLAlchemy async engine
- httpx para llamadas HTTP asíncronas
- LangChain async (`ainvoke`)

### 4. Manejo de Errores

- Excepciones específicas (`WhatsAppError`, `ArcadiumError`)
- Logging estructurado con `structlog`
- Rollback automático en LandChains (si se usa)
- Retry con `tenacity` para llamadas externas

### 5. Configuración Segura

- Pydantic Settings con validación
- **`.env` nunca commit** (en `.gitignore`)
- Valores por defecto sensatos
- Variables obligatorias fallan rápido

### 6. Testing y Debug

- Endpoint `/webhook/test` para pruebas sin side-effects
- `/debug/agent/{id}` para inspeccionar historial
- Logs JSON para ingestion en ELK/Loki
- Métricas Prometheus para observabilidad

### 7. Concurrencia Segura

- `contextvars` para estado por-thread/async
- Cada sesión tiene su propio agente (cache en dict)
- DB connection pool configurado
- No mutable global state

### 8. Producción Ready

- Docker multi-stage build
- Non-root user en contenedor
- Health checks en docker-compose
- Pool de DB ajustable
- Timeout configurable

---

## Despliegue

### Local (Desarrollo)

```bash
# 1. Instalar dependencias
bash install_simple.sh

# 2. Configurar .env (copiar de .env.example)
cp .env.example .env
# Editar .env con tus claves

# 3. Iniciar PostgreSQL y Redis (opcional)
docker-compose up -d postgres redis

# 4. Ejecutar en desarrollo con reload
./run.sh dev
```

API disponible en `http://localhost:8000`

### Producción (Docker)

```bash
# 1. Configurar .env (valores reales)
cp .env.example .env
# EDITAR .env con:
#   - OPENAI_API_KEY
#   - DATABASE_URL (Postgres real)
#   - WHATSAPP_API_URL (Evolution API)
#   - WHATSAPP_INSTANCE_NAME

# 2. Construir y levantar
docker-compose up -d

# 3. Ver logs
docker-compose logs -f arcadium-api
```

### Health Checks

```bash
curl http://localhost:8000/health
# {"status":"healthy","timestamp":"...","version":"2.0.0"}

curl http://localhost:9090/metrics  # Si ENABLE_METRICS=true
```

---

## Configuración

### Variables de Entorno (`.env`)

| Variable                  | Requerida | Descripción                                                     |
| ------------------------- | --------- | --------------------------------------------------------------- |
| `OPENAI_API_KEY`          | ✅        | API key de OpenAI                                               |
| `DATABASE_URL`            | ✅        | PostgreSQL (ej: `postgresql+psycopg2://user:pass@host:5432/db`) |
| `WHATSAPP_API_URL`        | ✅        | URL de Evolution API                                            |
| `WHATSAPP_INSTANCE_NAME`  | ✅        | Nombre de instancia en Evolution                                |
| `USE_POSTGRES_FOR_MEMORY` | ❌        | true para usar PostgreSQL para memoria                          |
| `DEBUG`                   | ❌        | false en producción                                             |
| `ENABLE_METRICS`          | ❌        | true para exponer /metrics                                      |
| `WEBHOOK_SECRET`          | ❌        | Secreto para verificar webhooks (opcional pero recomendado)     |

### Configurar Evolution API

1. Instalar Evolution API (Docker o binary)
2. Crear instancia (ej: `arcadium`)
3. Obtener API token (si requiere auth)
4. Configurar webhook en Evolution → `http://tu-dominio.com/webhook/whatsapp`

### Configurar PostgreSQL

```sql
-- Las tablas se crean automáticamente en inicio
-- Solo asegurar que el usuario tiene permisos:
GRANT ALL ON DATABASE arcadium TO arcadium_user;
```

---

## Scalabilidad

### Horizontal

- Stateless por session_id: agentes por sesión en memoria local
- Para múltiples instancias: usar Redis para memoria compartida
- Balanceador de carga (nginx) con sticky sessions (por phone_number)

### Vertical

- Ajustar `DB_POOL_SIZE` y `WORKERS`
- Usar OpenAI batch API si volumen alto
- Considerar Redis cache para tools frecuentes

---

## Migración desde n8n

| n8n           | Arcadium v2                                   |
| ------------- | --------------------------------------------- |
| Webhook node  | FastAPI `/webhook/whatsapp`                   |
| Function node | Python tools (agents)                         |
| HTTP Request  | httpx en services/                            |
| Set node      | SQLAlchemy models                             |
| n8n state     | MemoryManager (PostgreSQL)                    |
| Cron          | Docker/k8s + Celery (para tareas programadas) |

Pasos:

1. Identificar workflows de n8n
2. Mapear a tools en `agents/deyy_agent.py`
3. Migrar lógica de Function nodes a métodos de `AppointmentService`
4. Configurar Evolution API webhook apuntando a `https://tudominio.com/webhook/whatsapp`
5. Desactivar n8n

---

## Troubleshooting

### Error: "No module named 'langchain_postgres'"

```bash
pip install langchain-postgres
```

### Error: "Database connection failed"

Verificar `DATABASE_URL` y que Postgres esté corriendo.

### Mensajes no llegan a WhatsApp

Verificar `WHATSAPP_API_URL` y que Evolution API esté accesible desde el servidor.

### Agent no usa herramientas

Verificar `OPENAI_API_KEY` y que el modelo soporte function calling (GPT-4, GPT-3.5-turbo).

### Alto uso de memoria

Reducir `SESSION_EXPIRY_HOURS` o usar `USE_POSTGRES_FOR_MEMORY=true`.

---

## Próximos Pasos

- [ ] Implementar Meta Cloud API como alternativa a Evolution
- [ ] Añadir Redis cache para respuestas frecuentes
- [ ] Implementar Celery para tareas asíncronas pesadas
- [ ] Añadir rate limiting por IP
- [ ] Webhook signature verification
- [ ] Multi-tenant support (account_id)
- [ ] Dashboard de administración (FastAPI + React)

---

## Licencia

Propietario - Arcadium Labs
