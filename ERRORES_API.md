# Errores Pendientes - Admin API

## 📋 Lista de Errores Detectados

### Error 1: POST /api/v1/appointments → "name 'or\_' is not defined"

**Fecha detectada:** 2026-04-03
**Estado:** ❌ Pendiente (código corregido, necesita reinicio)

**Ubicación:** `services/project_appointment_service.py:264`

**Descripción:**
Al intentar crear una cita manualmente, el servicio `ProjectAppointmentService` usa `or_` de SQLAlchemy sin haberlo importado.

**Código problemático:**

```python
stmt = select(AppointmentModel).where(
    and_(
        AppointmentModel.project_id == self.project_id,
        AppointmentModel.status == "scheduled",
        or_(  # ← or_ no está importado
            and_(...),
            and_(...),
            and_(...)
        )
    )
)
```

**Imports actuales en el archivo:**

```python
from sqlalchemy.ext.asyncio import AsyncSession
# Falta: from sqlalchemy import and_, or_
```

**Corrección aplicada:**

```python
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_, or_  # ← Agregados
```

**Próximo paso:** Reiniciar el servidor para que cargue el cambio.

---

### Error 2: GET /api/v1/tools → ModuleNotFoundError: No module named 'langchain_core.pydantic_v1'

**Fecha detectada:** 2026-04-03
**Estado:** ⚠️ Resoluble (ajustar imports para LangChain v0.2+)

**Ubicación:** `agents/langchain_compat.py`

**Descripción:**
En versiones recientes de LangChain (>0.2), el módulo `langchain_core.pydantic_v1` fue removido. Se debe usar `pydantic` directamente.

**Solución propuesta:**

```python
try:
    # LangChain <0.2
    from langchain_core.pydantic_v1 import BaseModel, Field
except ImportError:
    # LangChain >=0.2
    from pydantic import BaseModel, Field
```

---

### Error 3: StateMachineAgent + StateGraph (Fase 4) - Versiones incompatibles

**Fecha detectada:** 2026-04-04
**Estado:** 🔄 En proceso de resolución

**Ubicación:** `tests/test_state_machine_integration.py`

**Descripción:**
Durante la ejecución de tests de integración se detectaron inconsistencias de versiones en dependencias:

- `langgraph-prebuilt 1.0.9` requiere `langchain-core>=1.0.0`
- `langchain 0.2.17` requiere `langchain-core<0.3.0,>=0.2.43`
- `langchain-openai 0.1.25` espera `langchain-core` en rango <0.3.0

**Síntomas:**

- `ImportError: cannot import name 'ContextOverflowError' from 'langchain_core.exceptions'`
- `ImportError: cannot import name 'format_tool_to_openai_tool' from 'langchain_core.tools'`

**Acciones tomadas:**

1. Recreado `venv` con `requirements.txt` oficial.
2. Instaladas versiones compatibles (langchain-core 0.2.43, langchain-openai 0.1.25, langgraph-prebuilt 1.0.9).
3. Las versiones siguen en conflicto.

**Próximos pasos sugeridos:**

- Revisar `requirements.txt` y especificar versiones exactas que funcionen juntas.
- Considerar eliminar dependencia de `langgraph-prebuilt` si no se usa.
- Actualizar `agents/langchain_compat.py` para usar la API de `langchain-core>=1.0` (revisar ubicación de herramientas y exceptions).
- Como alternativa, congelar a las versiones que tenía el sistema antes de los tests (pendiente).

---

### Error 4: `add_error` retorna `state` en lugar de `List[str]`

**Fecha detectada:** 2026-04-04
**Estado:** ✅ Corregido en `agents/support_state.py`

**Ubicación:** `agents/support_state.py:224-231`

**Descripción:**
La función `add_error` devolvía el estado completo, pero en `tools_state_machine.py` se usaba:

```python
"errors_encountered": add_error(runtime.state, "fecha_pasada")
```

Esto convertía `errors_encountered` en `dict`, rompiendo expects de tipo `List[str]`.

**Corrección:**

```python
def add_error(state: Dict[str, Any], error: str) -> List[str]:
    errors = state.get("errors_encountered", [])
    errors.append(error)
    state["errors_encountered"] = errors
    return errors
```

---

## Cómo reportar nuevos errores

1. **Nombre y ubicación** del error (archivo:línea).
2. **Mensaje de error completo**.
3. **Pasos para reproducir**.
4. **Solución propuesta** (si aplica).

**Fecha detectada:** 2026-04-03
**Estado:** ✅ Corregido (cambio de implementación)

**Ubicación:** `admin/api.py` - función `list_tools()`

**Descripción:**
Al importar `deyy_agent` desde el endpoint `/tools`, se carga LangChain completo que requiere `langchain_core.pydantic_v1` (compatibilidad Pydantic v1 vs v2).

**Problema:**

- El entorno tiene Pydantic v2
- `langchain-openai` versiones antiguas usan Pydantic v1 internamente
- Al importar `deyy_agent`, se carga todo el stack de LangChain que falla

**Solución implementada:**
En lugar de importar las herramientas de `deyy_agent` (que causa la dependencia circular y problemas de módulos), se cambió a:

```python
@router.get("/tools")
async def list_tools(
    project: Project = Depends(verify_api_key),
    db_session: AsyncSession = Depends(get_db_session)
) -> List[Dict[str, Any]]:
    # Obtener config del proyecto
    stmt = select(ProjectAgentConfig).where(ProjectAgentConfig.project_id == project.id)
    result = await db_session.execute(stmt)
    config = result.scalar_one_or_none()

    if not config:
        config = await _create_default_config(project, db_session)

    # Devolver lista desde config.enabled_tools
    tools_info = []
    for tool_name in config.enabled_tools:
        descriptions = {...}  # Descripciones hardcodeadas
        tools_info.append({"name": tool_name, "description": ...})

    return tools_info
```

**Estado actual:** ✅ Funcionando (ya no importa `deyy_agent`)

---

### Error 3: POST /api/v1/appointments - Parámetros en query en lugar de body

**Fecha detectada:** 2026-04-03
**Estado:** ✅ Corregido (necesita reinicio)

**Ubicación:** `admin/api.py:340`

**Descripción:**
FastAPI por defecto interpreta los parámetros de función como query parameters. El endpoint debería recibir un JSON body.

**Código original:**

```python
async def create_appointment_manual(
    phone_number: str,  # ← FastAPI asume query param
    appointment_datetime: str,
    ...
)
```

**Corrección aplicada:**

```python
from fastapi import Body  # ← Import agregado

async def create_appointment_manual(
    phone_number: str = Body(...),
    appointment_datetime: str = Body(...),
    service_type: str = Body(...),
    notes: Optional[str] = Body(None),
    ...
)
```

**Próximo paso:** Reiniciar servidor para que tome el cambio de Body parameters.

---

### Error 4: GET /tools - StructuredTool no tiene **name**

**Fecha detectada:** 2026-04-03
**Estado:** ✅ Corregido con la solución al Error 2

**Descripción:**
Las herramientas decoradas con `@tool` de LangChain son objetos `StructuredTool`, no funciones Python, por lo que no tienen atributo `__name__`.

**Solución:** Cambiado a usar `config.enabled_tools` en lugar de importar las herramientas.

---

## 🛠️ Cambios Aplicados (No Requieren Reinicio)

### 1. Dependencia verify_api_key

✅ Corregida en `admin/api.py:31-46`

Antes de la corrección, el endpoint `update_agent_config` hacía:

```python
project = await verify_api_key(request=request)  # Llamada manual
```

Después:

```python
async def update_agent_config(
    updates: Dict[str, Any],
    project: Project = Depends(verify_api_key),  # ← Usa Depends
    ...
)
```

**Ventaja:** FastAPI inyecta automáticamente `request` a `verify_api_key`.

---

### 2. Endpoint /tools rediseñado

✅ Cambiado para evitar importación de `deyy_agent`

Nueva implementación:

- Lee `ProjectAgentConfig` de la base de datos
- Devuelve lista de herramientas desde `config.enabled_tools`
- Descripciones hardcodeadas en diccionario
- Sin dependencias circulares

---

### 3. Import de Body agregado

✅ `from fastapi import Body` agregado en `admin/api.py:9`

---

### 4. Import de or\_ agregado

✅ `from sqlalchemy import select, and_, or_` en `services/project_appointment_service.py:11`

---

## ⚠️ Problemas de Diseño Detectados

### 1. Hardcodeo de descripciones de herramientas

**Ubicación:** `admin/api.py` función `list_tools()`

**Problema:** Las descripciones de las herramientas están hardcodeadas en un diccionario.

**Solución futura:** Guardar descripciones en la tabla `ProjectAgentConfig` o derivar de la metadata de las herramientas (si se logra importar correctamente sin problemas de Pydantic).

---

### 2. Falta logger en algunos endpoints

**Estado:**⚠️ Parcial

Algunos endpoints no tienen logging estructurado. Solo `/tools` tiene logger. Se debería agregar a todos para auditoría.

---

### 3. Paginación sin total_count real

**Ubicación:** `list_conversations()` y `list_appointments()`

**Problema:** Devuelve `"total": len(conv_list)` que es solo el count de la página actual, no el total de registros en DB.

**Ejemplo:**

```python
stmt = select(Conversation).where(...).limit(limit).offset(offset)
conversations = result.scalars().all()
# total = len(conversations)  ← Esto es solo la página, NO el total
```

**Solución:** Hacer un `COUNT(*)` separado:

```python
total_stmt = select(func.count()).select_from(Conversation).where(...)
total = await db_session.scalar(total_stmt)
```

---

## 📊 Tabla de Estado de Endpoints

| Endpoint                                  | Método | Estado               | Notas              |
| ----------------------------------------- | ------ | -------------------- | ------------------ |
| `/api/v1/projects/current`                | GET    | ✅ OK                | Funcionando        |
| `/api/v1/agent/config`                    | GET    | ✅ OK                | Funcionando        |
| `/api/v1/agent/config`                    | PUT    | ⚠️ Necesita reinicio | Cuerpo body OK     |
| `/api/v1/conversations`                   | GET    | ✅ OK                | Funcionando        |
| `/api/v1/conversations/{id}`              | GET    | ✅ OK                | Funcionando        |
| `/api/v1/conversations/{id}/agent-toggle` | POST   | ✅ OK                | Funcionando        |
| `/api/v1/conversations/{id}/messages`     | GET    | ✅ OK                | Funcionando        |
| `/api/v1/conversations/{id}/memory`       | GET    | ✅ OK                | Funcionando        |
| `/api/v1/conversations/{id}/memory`       | DELETE | ✅ OK                | Funcionando        |
| `/api/v1/appointments`                    | GET    | ✅ OK                | Funcionando        |
| `/api/v1/appointments`                    | POST   | ⚠️ Necesita reinicio | Falta import `or_` |
| `/api/v1/stats`                           | GET    | ✅ OK                | Funcionando        |
| `/api/v1/tools`                           | GET    | ✅ OK                | Funcionando        |
| `/api/v1/audit/logs`                      | GET    | ✅ OK                | Funcionando        |

---

## 🔄 Pasos para Reiniciar el Servidor (Seguros)

**IMPORTANTE:** Antes de reiniciar, verifica que no haya otros procesos críticos dependiendo del servidor.

1. **Verificar procesos:**

   ```bash
   ps aux | grep arcadium
   lsof -i :8000
   ```

2. **Parar servidor:**

   ```bash
   ./run.sh stop  # Si existe el script
   # O
   pkill -f "python -m arcadium_automation start"
   ```

3. **Verificar que el puerto 8000 esté libre:**

   ```bash
   lsof -ti:8000  # Debe no devolver nada
   ```

4. **Iniciar servidor:**

   ```bash
   ./run.sh start
   ```

5. **Verificar logs:**

   ```bash
   tail -f logs/arcadium_automation.log
   ```

6. **Probar endpoints:**
   ```bash
   python test_admin_api.py
   ```

---

## 📁 Archivos Modificados

### Modificados:

1. ✅ `admin/api.py` - API completa reescrita
2. ✅ `services/project_appointment_service.py` - Agregado import de `or_`

### Creados:

1. ✅ `test_admin_api.py` - Suite de pruebas
2. ✅ `create_test_project.py` - Helper para proyecto de prueba
3. ✅ `ADMIN_API_IMPLEMENTATION.md` - Documentación completa
4. ✅ `ERRORES_API.md` - Este archivo

---

## 🎯 Checklist para Completar

- [x] Implementar verify_api_key como dependencia
- [x] Crear endpoint /projects/current
- [x] Crear endpoint /agent/config (GET)
- [x] Crear endpoint /agent/config (PUT)
- [x] Crear endpoint /conversations (GET)
- [x] Crear endpoint /conversations/{id} (GET)
- [x] Crear endpoint /conversations/{id}/agent-toggle (POST)
- [x] Crear endpoint /conversations/{id}/messages (GET)
- [x] Crear endpoint /conversations/{id}/memory (GET)
- [x] Crear endpoint /conversations/{id}/memory (DELETE)
- [x] Crear endpoint /appointments (GET)
- [x] Crear endpoint /appointments (POST)
- [x] Crear endpoint /stats (GET)
- [x] Crear endpoint /tools (GET) - sin importar deyy_agent
- [x] Crear endpoint /audit/logs (GET)
- [ ] Reiniciar servidor para aplicar cambios
- [ ] Verificar que POST /appointments funcione
- [ ] Agregar logging estructurado a todos los endpoints
- [ ] Corregir paginación para devolver total_count real
- [ ] Configurar CORS si es necesario
- [ ] Servir archivos estáticos del admin panel

---

**Última actualización:** 2026-04-03 23:35
**Responsable:** Claude Code
