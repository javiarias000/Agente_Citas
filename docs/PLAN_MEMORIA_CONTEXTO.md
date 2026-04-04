clea# Plan de Solución: Contexto y Memoria en Conversaciones

**Fecha:** 2026-04-03  
**Problema:** El agente pierde el contexto entre mensajes y responde como si fuera una nueva conversación  
**Estado:** EN ANÁLISIS → PLANIFICACIÓN

---

## 1. Diagnóstico del Problema

### Síntomas reportados

```
Usuario: "Quiero agendar una cita para hoy, para sacarme una muela. A las 12 del dia, por favor"
AI: "Hay disponibilidad para la extracción a las 12:00 PM hoy. ¿Confirmas agendar la cita para hoy a las 12:00 PM para la extracción?"
Usuario: "si porplease"
AI: "¡Hola! ¿En qué puedo ayudarte hoy?"  # <-- PERDIÓ CONTEXTO
```

### Arquitectura actual (Funcionamiento esperado)

```
WhatsApp → Webhook → orchestrator._handle_whatsapp_webhook()
  ↓
1. Extrae phone_number del payload
2. session_id = phone_number (Ej: "+34612345678")
3. Obtiene/crea agente: agent = self._get_or_create_agent(session_id)
4. Agent.process_message(message)
  ↓
  a) Carga historial: memory_manager.get_history(session_id)
  b) Invoca LLM con: {input + chat_history}
  c) Guarda mensajes en memoria: memory_manager.add_message()
  ↓
Respuesta al usuario
```

**Importante:** El sistema _debería_ funcionar porque:

- `session_id` = `phone_number` (único por usuario)
- Agentes se cachean en `self._agents[session_id]`
- `MemoryManager` es compartido y persistente
- Cada `process_message()` carga el historial completo

---

## 2. Posibles Causas Raíz

### Hipótesis A: Inconsistencia en `session_id`

**Problema:** El `session_id` puede estar cambiando entre mensajes

** Lugares donde se genera:**

1. `orchestrator._handle_whatsapp_webhook()`: `session_id = message_data["sender"]`
2. `orchestrator._handle_test_webhook()`: `session_id = payload.get("session_id", "test_session")`
3. WebSocket: `session_id` viene del path parameter `/ws/{session_id}`

**Normalización de teléfonos:**

- ¿El payload de WhatsApp envía siempre el mismo formato?
- ¿Incluye código de país siempre? (`+34` vs `34` vs `612345678`)
- ¿Espacios, guiones, paréntesis?
- ¿Qué hace `_extract_phone_from_session()` en `deyy_agent.py`?

**⚠️ CRÍTICO:** El método `_extract_phone_from_session()` (línea 1111-1120) **NO normaliza**:

```python
def _extract_phone_from_session(self, session_id: str) -> str:
    if "@" not in session_id and session_id.replace("+", "").isdigit():
        return session_id
    return session_id
```

Esto significa:

- Si viene `"+34612345678"` → lo usa tal cual
- Si viene `"34612345678"` → lo usa tal cual
- **Si cambia el formato entre mensajes, ¡son diferentes session_id!**

---

### Hipótesis B: Agente nuevo sin memoria

**Problema:** `_get_or_create_agent()` crea agentes nuevos si no existen en caché

```python
async def _get_or_create_agent(self, session_id: str) -> Any:
    if session_id not in self._agents:  # <-- Si cambia session_id, crea nuevo
        agent = DeyyAgent(session_id=session_id, memory_manager=self.memory_manager, ...)
        self._agents[session_id] = agent
    return self._agents[session_id]
```

**¿Cuándo se pierde la caché?**

- Reinicio del servidor → `self._agents` se vacía
- Si no hay persistencia de agentes, cada sesión vuelve a empezar
- **PERO:** La memoria (`MemoryManager`) **sí es persistente** (PostgreSQL o InMemory)
- Si el agente es nuevo pero el `session_id` es el mismo, `memory_manager.get_history()` debería devolver el historial completo

---

### Hipótesis C: Error silencioso en memoria

**Problema:** `memory_manager.add_message()` podría fallar sin levantar error

**Revisar:**

- ¿Hay `try/except` que capture excepciones en `add_message`?
- ¿La transacción de DB se commitéa?
- En `PostgresStorage.add_message()` (postgres_memory.py línea 102-117):
  ```python
  async with get_async_session() as session:
      record = LangchainMemory(...)
      session.add(record)
      await session.flush()  # ¿Hay commit? NO - solo flush
  ```
  **⚠️ No hay `session.commit()`** → Los mensajes podrían no persistirse

**En `memory_manager.add_message()`** (línea 167-188):

```python
await self._backend.add_message(session_id, message)
```

No hay `try/catch`, por lo que si falla, debería propagar el error.

---

### Hipótesis D: El agente no está cargando el historial

**En `DeyyAgent.process_message()`** (deyy_agent.py línea 1006-1009):

```python
result = await self._agent_executor.ainvoke({
    "input": message,
    "chat_history": await self.memory_manager.get_history(self.session_id)
})
```

Parece correcto. ¿Pero qué pasa si `get_history()` devuelve lista vacía?

**Verificar:**

- ¿`get_history()` realmente consulta la DB?
- ¿Hay algún filtro por `session_id` que no coincida?
- ¿El `session_id` que usa el agente es el mismo que se guardó?

---

### Hipótesis E: Caché local de StateManager

**Posible interferencia:** `StateManager` tiene caché local (`_local_cache`) que podría servir datos obsoletos

**No aplica** aquí porque `MemoryManager` no usa `StateManager`, usa su propio backend directo a DB.

---

## 3. Plan de Diagnóstico

### Paso 1: Agregar logging detallado

**En `orchestrator.py`:**

- Log del `session_id` que se extrae en `_handle_whatsapp_webhook`
- Log del session_id usado en `_get_or_create_agent`

**En `deyy_agent.py`:**

- Log del historial recuperado: `len(history)` en `process_message()`
- Log de los primeros mensajes del historial (no contenido completo por privacidad)
- Log de confirmación de guardado

**En `postgres_memory.py`:**

- Log de cada `get_history`: `session_id`, `count`
- Log de cada `add_message`: `session_id`, `type`, `len(content)`
- Log de errores en DB con `session.commit()`

### Paso 2: Verificar normalización de phone_number

**Agregar función de normalización:**

```python
def normalize_phone(phone: str) -> str:
    """Normaliza número de teléfono a formato internacional E.164"""
    # Limpiar: quitar espacios, guiones, paréntesis
    cleaned = re.sub(r'[\s\-\(\)]', '', phone)
    # Asegurar prefijo + si no lo tiene
    if not cleaned.startswith('+'):
        # Asumir código de país por defecto (España: +34)
        cleaned = '+34' + cleaned if not cleaned.startswith('34') else '+' + cleaned
    return cleaned
```

**Aplicar en:**

- `_parse_whatsapp_payload()` → normalizar `sender`
- `_get_or_create_agent()` → quizás normalizar `session_id`
- `_extract_phone_from_session()` → normalizar antes de usar

---

### Paso 3: Verificar commit en PostgreSQL

**En `postgres_memory.py`, método `add_message()`:**

```python
async with get_async_session() as session:
    record = LangchainMemory(...)
    session.add(record)
    await session.flush()
    await session.commit()  # ⬅️ AGREGAR ESTO
```

**¿Por qué falta?**

- El patrón actual usa sesiones cortas que se autocommitan?, hay que revisar `get_async_session()`

**Investigar:** `db/__init__.py` → `get_async_session()` implementation

---

### Paso 4: Verificar que el historial incluye más de 1 mensaje

**Agregar métrica debug:**

```python
history = await self.memory_manager.get_history(self.session_id)
logger.info("Historial cargado", session_id=self.session_id, count=len(history))
if len(history) >= 2:
    logger.debug("Últimos mensajes",
                 last_user=history[-2].content[:50] if len(history) >= 2 else None,
                 last_ai=history[-1].content[:50] if len(history) >= 1 else None)
```

---

## 4. Plan de Implementación

### Fase 1: Diagnóstico Inicial (Prioridad Alta)

1. **Agregar logging estructurado** en puntos clave
   - `orchestrator.py`: log de `sender` extraído
   - `deyy_agent.py`: log de tamaño de historial
   - `postgres_memory.py`: log de operaciones

2. **Ejecutar prueba manual** con logs detallados

   ```bash
   LOG_LEVEL=DEBUG ./run.sh start
   # Enviar 2 mensajes consecutivos (webhook/test)
   # Ver logs: ¿session_id iguales? ¿historial tamaño?
   ```

3. **Inspeccionar DB directamente**
   ```sql
   SELECT session_id, type, content, created_at
   FROM langchain_memory
   WHERE session_id = '+34612345678'
   ORDER BY created_at;
   ```

---

### Fase 2: Normalización de Teléfonos (Prioridad Alta)

**-si se confirma inconsistencia-**

1. Crear `utils/phone_normalizer.py` con función `normalize_phone()`
2. Aplicar normalización en:
   - `orchestrator._parse_whatsapp_payload()` -> `sender`
   - `deyy_agent._extract_phone_from_session()`
   - Cualquier lugar que Use phone como identificador

3. Actualizar tests para cubrir formatos mixtos

---

### Fase 3: Commit de Memoria (Prioridad Alta)

**-si se confirma que no hay commit-**

1. En `postgres_memory.py`, en `add_message()`:
   - Agregar `await session.commit()` después de `session.flush()`
   - O configurar `autocommit=True` en la sesión

2. Revisar `db/__init__.py` → `get_async_session()`:
   - ¿Devuelve sesión con `autocommit` o `expire_on_commit=False`?
   - Asegurar que cada operación se persista

---

### Fase 4: Asegurar Reutilización de Agentes (Prioridad Media)

**Problema potencial:** `self._agents` es un diccionario en memoria

- Si hay múltiples workers (Gunicorn), cada worker tiene su propia caché
- **Solución:** No hay - asumir que en producción se usa single worker, o
  Implementar sticky sessions en el load balancer

**Para esta instalación:** Revisar configuración de workers

- `uvicorn` con `workers=1` por defecto → OK

---

### Fase 5: Añadir TTL de agente (Prioridad Baja)

Opcional: limpiar agentes inactivos después de `SESSION_EXPIRY_HOURS`

---

## 5. Criterios de Éxito

### Test de conversación multi-mensaje

```bash
# Mensaje 1
curl -X POST http://localhost:8000/webhook/test \
  -H "Content-Type: application/json" \
  -d '{"sender": "+34612345678", "message": "Quiero agendar una cita", "message_type": "text"}'
# -> AI responde preguntando fecha/hora

# Mensaje 2 (mismo sender)
curl -X POST http://localhost:8000/webhook/test \
  -H "Content-Type: application/json" \
  -d '{"sender": "+34612345678", "message": "Mañana a las 12", "message_type": "text"}'
# -> AI recuerda que quiere agendar y responde en consecuencia
```

**Validación:**

- El segundo mensaje debe incluir contexto del primero
- La respuesta debe ser relevante a la conversación previa
- En DB: `langchain_memory` debe tener al menos 4 entradas (2 human, 2 ai)

---

### Logs esperados

```
orchestrator: Webhook recibido, sender="+34612345678"
orchestrator: Obteniendo/creando agente, session_id="+34612345678"
deyy_agent: Historial cargado, session_id="+34612345678", count=2
deyy_agent: Procesando mensaje: "Mañana a las 12"
memory.manager: Mensaje guardado, session_id="+34612345678", type="human"
deyy_agent: Respuesta generada (usa historial de 2 mensajes)
memory.manager: Mensaje guardado, session_id="+34612345678", type="ai"
```

---

## 6. Checklist de Ejecución

- [ ] **Diagnóstico 1:** Agregar logs detallados
- [ ] **Diagnóstico 2:** Ejecutar prueba de 2+ mensajes y capturar logs
- [ ] **Diagnóstico 3:** Consultar DB directamente (`langchain_memory`)
- [ ] **Diagnóstico 4:** Verificar que `session_id` sea idéntico en todos los mensajes
- [ ] **Diagnóstico 5:** Verificar que `add_message` haga commit

**Según hallazgos:**

- [ ] **Normalización:** Implementar si hay inconsistencia
- [ ] **Commit:** agregar `session.commit()` si falta
- [ ] **Testing:** Probar de nuevo
- [ ] **Documentación:** Actualizar docs sobre formato de teléfono

---

## 7. Riesgos y Mitigaciones

| Riesgo                                       | Impacto | Mitigación                                                                                                      |
| -------------------------------------------- | ------- | --------------------------------------------------------------------------------------------------------------- |
| Normalización rompe IDs existentes           | Alto    | Migración DB: actualizar `langchain_memory.session_id` con números normalizados                                 |
| Commit doble causa errores                   | Medio   | Probar en ambiente de prueba primero                                                                            |
| Cambios en phone_number afectan otras tablas | Medio   | Aplicar normalización solo en memoria, no en DB (Conversation, Appointment usan phone normalizado desde origen) |
| Logs exponen datos sensibles                 | Bajo    | Enmascarar contenido en logs de producción                                                                      |

---

## 8. Cronograma Estimado

| Actividad                  | Tiempo         |
| -------------------------- | -------------- |
| Agregar logs + deploy      | 30 min         |
| Pruebas + análisis de logs | 20 min         |
| Implementación de fix(es)  | 1-2 horas      |
| Testing completo           | 30 min         |
| Documentación              | 20 min         |
| **Total**                  | **~3-4 horas** |

---

## 9. Referencias de Código

**Puntos clave a modificar:**

- `arcadium_automation/core/orchestrator.py:457-460` - Extracción de `sender`
- `arcadium_automation/core/orchestrator.py:608-630` - `_get_or_create_agent()`
- `arcadium_automation/agents/deyy_agent.py:977-1027` - `process_message()`
- `arcadium_automation/agents/deyy_agent.py:1111-1120` - `_extract_phone_from_session()`
- `arcadium_automation/memory/postgres_memory.py:74-118` - `get_history()` y `add_message()`
- `arcadium_automation/db/__init__.py` - `get_async_session()`

---

**Próximo paso:** Ejecutar diagnóstico con logs en DEBUG y obtener evidencias concretas del problema.
