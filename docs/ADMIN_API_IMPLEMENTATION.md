# Admin API - Implementation Documentation

## Resumen

Se implementó la API de administración para el panel de control de Arcadium Automation. La API incluye autenticación mediante API Key, gestión de proyectos, configuraciones, conversaciones, citas y herramientas.

---

## ✅ Implementado Correctamente

### 1. **Archivo `admin/api.py`**

- Estructura completa del módulo con router FastAPI
- Dependencia `verify_api_key` funcionando correctamente
- Todos los endpoints devuelven datos en formato JSON

### 2. **Dependencia de Autenticación** (`verify_api_key`)

```python
async def verify_api_key(
    x_api_key: str = Header(..., alias="X-API-Key"),
    request: Request = None
) -> Project:
```

- Usa `Header(..., alias="X-API-Key")` para requerir la cabecera obligatoria
- Busca el proyecto por API key hasheada (SHA256)
- Valida que el proyecto esté activo
- Inyecta automáticamente mediante `Depends(verify_api_key)`

### 3. **Endpoints Funcionando**

#### GET /api/v1/projects/current

- Obtiene detalles del proyecto actual basado en API key
- Respuesta: `{id, name, slug, is_active, whatsapp_webhook_url, settings, created_at, updated_at}`

#### GET /api/v1/agent/config

- Obtiene configuración del agente para el proyecto
- Si no existe, crea configuración por defecto
- Respuesta completa con todos los campos

#### PUT /api/v1/agent/config

- Actualiza configuración del agente
- Campos permitidos: `system_prompt`, `custom_instructions`, `max_iterations`, `temperature`, `enabled_tools`, `calendar_enabled`, `google_calendar_id`, `calendar_timezone`, `calendar_mapping`, `global_agent_enabled`
- **Corregido**: Usa `Depends(verify_api_key)` en lugar de llamada manual

#### GET /api/v1/conversations

- Lista conversaciones del proyecto
- Filtrado por estado (`status` query param)
- Paginación con `limit` y `offset`
- Incluye último mensaje de cada conversación
- **Helper**: `_get_last_message()` para obtener el mensaje más reciente

#### GET /api/v1/conversations/{conversation_id}

- Obtiene detalles de una conversación específica
- Verifica pertenencia al proyecto

#### POST /api/v1/conversations/{conversation_id}/agent-toggle

- Activa/desactiva el agente para una conversación
- Crea o actualiza registro en `AgentToggle`

#### GET /api/v1/conversations/{conversation_id}/messages

- Lista mensajes de una conversación
- Verifica que la conversación pertenezca al proyecto

#### GET /api/v1/conversations/{conversation_id}/memory

- Obtiene memoria de conversación en formato timeline
- Muestra historial de `LangchainMemory`

#### DELETE /api/v1/conversations/{conversation_id}/memory

- Limpia memoria de conversación

#### GET /api/v1/appointments

- Lista citas del proyecto
- Filtros opcionales: `phone_number`, `status`

#### POST /api/v1/appointments

- Crea cita manualmente
- **Corregido**: Parámetros en body (JSON) no query
- Validación de datetime ISO 8601
- Usa `ProjectAppointmentService` para crear cita

#### GET /api/v1/stats

- Estadísticas del proyecto
- Conversaciones activas
- Mensajes hoy
- Citas programadas
- **Optimizado**: Uso de `db_session.scalar()` para queries de conteo

#### GET /api/v1/audit/logs

- Logs de herramientas (tool calls)
- Filtro opcional por `tool_name`
- Ordenado por fecha descendente

### 4. **Helpers Implementados**

- `_get_last_message()`: Obtiene el último mensaje de una conversación
- `_config_to_dict()`: Convierte modelo SQLAlchemy a dict para API response
- `_create_default_config()`: Crea configuración por defecto con prompt inicial

### 5. **Registro en Orchestrator**

El router se registra correctamente en `core/orchestrator.py`:

```python
try:
    from admin.api import router as admin_router
    app.include_router(admin_router)
    logger.info("Admin API router registrado")
except ImportError as e:
    logger.warning("Admin API router no disponible", error=str(e))
```

---

## ❌ Errores y Problemas Pendientes

### **Error 1: GET /api/v1/tools - Internal Server Error (500)**

**Ubicación:** `admin/api.py` línea 378-382

**Problema:**

```python
@router.get("/tools")
async def list_tools() -> List[Dict[str, Any]]:
    from agents.deyy_agent import consultar_disponibilidad, agendar_cita, ...
```

Al importar las herramientas desde `deyy_agent`, ocurre un error probablemente porque:

- Las herramientas son decoradas con `@tool` de LangChain
- Pueden no estar definidas como funciones exportables
- Hay dependencias circulares al importar

**Solución Propuesta:**

1. Revisar `agents/deyy_agent.py` para ver cómo están definidas las herramientas
2. Mover las importaciones al nivel del módulo (arriba del archivo) para detectar errores temprano
3. O capturar el error específico y loguearlo
4. Alternativa: Devolver lista hardcodeada o usar `inspect` para obtener herramientas del agente

**Investigar:**

- ¿Las funciones `consultar_disponibilidad`, `agendar_cita`, etc. están definidas como `@tool`?
- ¿Hay dependencias circulares entre `admin/api.py` y `agents/deyy_agent.py`?

---

### **Error 2: POST /api/v1/appointments - Missing Body Parameters (422)**

**Ubicación:** `admin/api.py` línea 340-372

**Problema Original:**
Los parámetros estaban definidos como query parameters por defecto:

```python
async def create_appointment_manual(
    phone_number: str,  # <- FastAPI asume query param
    appointment_datetime: str,
    ...
)
```

**Corregido a:**

```python
async def create_appointment_manual(
    phone_number: str = Body(...),
    appointment_datetime: str = Body(...),
    service_type: str = Body(...),
    notes: Optional[str] = Body(None),
    ...
)
```

**Estado:** Requiere reiniciar el servidor para que tome el cambio.

---

### **Mejoras Opcionales**

#### 1. **Logging en todos los endpoints**

Actualmente solo el endpoint `/tools` tiene logger. Se debería agregar logging estructurado a todos los endpoints para auditoría.

#### 2. **Validación de tipos Pydantic**

Podrían definirse modelos Pydantic para los bodies de los endpoints:

- `AgentConfigUpdate` para PUT `/agent/config`
- `AppointmentCreate` para POST `/appointments`
- Mejora la documentación automática de Swagger

#### 3. **Paginación en [/conversations] y [/appointments]**

Ya existen `limit` y `offset`, pero no se devuelve `total_count` en la respuesta (solo se devuelve `len(conv_list)`). Para paginación real, debería consultarse el COUNT total de forma separada.

#### 4. **Cache de consultas frecuentes**

Las consultas de stats podrían cachearse por unos segundos para reducir carga en DB.

#### 5. **Rate Limiting**

La API no tiene rate limiting. Se podría agregar middleware para protegerse de abusos.

#### 6. **Endpoint para servir frontend estático**

Actualmente los templates estáticos en `templates/` no se sirven. Se podría agregar:

```python
from fastapi.staticfiles import StaticFiles
app.mount("/admin", StaticFiles(directory="templates/admin", html=True), name="admin")
```

#### 7. **CORS para Admin UI**

Si el frontend se sirve desde otro dominio, necesita CORS configurado:

```python
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
```

---

## 📋 Archivos Modificados/Creados

### Modificados

1. ✅ `admin/api.py` - API completa reescrita y organizada

### Creados

1. ✅ `test_admin_api.py` - Script de prueba automatizado
2. ✅ `create_test_project.py` - Utilidad para crear proyecto de prueba con API key

---

## 🧪 Testing

### Comandos de prueba

```bash
# 1. Crear proyecto de prueba (una sola vez)
python create_test_project.py

# 2. Iniciar servidor
./run.sh start

# 3. Ejecutar test
python test_admin_api.py
```

### Respuesta esperada

Todos los endpoints deben devolver status 200 excepto `/tools` que tiene error pendiente.

### Verificación manual con curl

```bash
# Obtener project actual
curl -H "X-API-Key: test-key-123" http://localhost:8000/api/v1/projects/current

# Obtener agente config
curl -H "X-API-Key: test-key-123" http://localhost:8000/api/v1/agent/config

# Actualizar agente config
curl -X PUT -H "X-API-Key: test-key-123" \
  -H "Content-Type: application/json" \
  -d '{"temperature": 0.8}' \
  http://localhost:8000/api/v1/agent/config

# Listar conversaciones
curl -H "X-API-Key: test-key-123" "http://localhost:8000/api/v1/conversations?limit=10"

# Crear cita (con body JSON)
curl -X POST -H "X-API-Key: test-key-123" \
  -H "Content-Type: application/json" \
  -d '{"phone_number":"+573123456789","appointment_datetime":"2026-04-05T10:00:00","service_type":"consulta"}' \
  http://localhost:8000/api/v1/appointments
```

---

## 🔧 Pasos para Completar

1. **Reiniciar servidor** para que tome cambios en `admin/api.py`
2. **Investigar error en endpoint `/tools`**:
   - Revisar definición de herramientas en `agents/deyy_agent.py`
   - Verificar si hay dependencias circulares
   - Implementar manejo de error adecuado
3. **Agregar modelos Pydantic** para validación de request bodies
4. **Agregar logging** en todos los endpoints
5. **Configurar CORS** si es necesario para el frontend
6. **Servir archivos estáticos** del admin panel

---

## 📊 Estado Actual

| Endpoint                                  | Método | Estado | Notas                                     |
| ----------------------------------------- | ------ | ------ | ----------------------------------------- |
| `/api/v1/projects/current`                | GET    | ✅     | Funcionando                               |
| `/api/v1/agent/config`                    | GET    | ✅     | Funcionando                               |
| `/api/v1/agent/config`                    | PUT    | ✅     | Funcionando (corregido)                   |
| `/api/v1/conversations`                   | GET    | ✅     | Funcionando                               |
| `/api/v1/conversations/{id}`              | GET    | ✅     | Funcionando                               |
| `/api/v1/conversations/{id}/agent-toggle` | POST   | ✅     | Funcionando                               |
| `/api/v1/conversations/{id}/messages`     | GET    | ✅     | Funcionando                               |
| `/api/v1/conversations/{id}/memory`       | GET    | ✅     | Funcionando                               |
| `/api/v1/conversations/{id}/memory`       | DELETE | ✅     | Funcionando                               |
| `/api/v1/appointments`                    | GET    | ✅     | Funcionando                               |
| `/api/v1/appointments`                    | POST   | ⚠️     | Body params corregidos, necesita reinicio |
| `/api/v1/stats`                           | GET    | ✅     | Funcionando                               |
| `/api/v1/tools`                           | GET    | ❌     | Error 500 al importar herramientas        |
| `/api/v1/audit/logs`                      | GET    | ✅     | Funcionando                               |

---

## 🎯 Objetivo Final

Tener una API de administración completa que permita:

- Gestión de configuración de agentes desde frontend
- Visualización de conversaciones y mensajes
- Creación manual de citas
- Monitoreo de estadísticas
- Auditoría de tool calls
- Gestión de memoria de conversaciones

---

**Fecha:** 2026-04-03
**Autor:** Claude Code
**Versión:** 1.0
