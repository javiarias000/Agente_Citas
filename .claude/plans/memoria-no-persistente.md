# Plan: Solucionar Problema de Memoria No Persistente en DeyyAgent

## Contexto

El usuario reporta que el agente Deyy no recuerda el contexto de la conversación entre mensajes. Específicamente:

- Usuario: "Quiero agendar una cita para hoy a las 12 del día."
- Agente: Pregunta el tipo de servicio.
- Usuario: "Empaste, a nombre de Jorge Javier Arias."
- Agente: Vuelve a preguntar fecha y hora (como si no recordara "hoy a las 12").

El problema puede ser:

1. **La memoria NO se está guardando** (postgres_memory.add_message no persiste)
2. **La memoria NO se está recuperando** (get_history devuelve lista vacía)
3. **El session_id cambia entre mensajes** (normalización inconsistente)
4. **El agente no está usando el historial** aunque esté disponible (prompt/LLM)
5. **InMemoryStorage se usa en lugar de PostgreSQL** y el servidor se reinicia o usa múltiples workers

---

## Análisis del Código Existente

### Flujo correcto (esperado):

1. **Webhook** (`orchestrator._handle_whatsapp_webhook`):
   - `message_data["sender"]` = número normalizado via `normalize_phone()`
   - `session_id = message_data["sender"]` (linea 469)
   - `agent = await self._get_or_create_agent(session_id)` (linea 492)
   - `result = await agent.process_message(message)` (linea 493)

2. **Agent.process_message** (`deyy_agent.py:978`):
   - Carga historial: `history = await self.memory_manager.get_history(self.session_id)` (linea 1007)
   - Invoca: `result = await self._agent_executor.ainvoke({"input": message, "chat_history": history})` (linea 1024)
   - Guarda: `await self.memory_manager.add_message(session_id, content, message_type)` (lineas 1036-1045)

3. **MemoryManager → PostgreSQLMemory** (`postgres_memory.py`):
   - `get_history`: SELECT por `session_id` ordenado por `created_at`
   - `add_message`: INSERT + commit

### Problemas potenciales detectados:

- **Normalización doble**: `_extract_phone_from_session` vuelve a normalizar un `session_id` que ya está normalizado. Es idempotente, pero podría causar discrepancias si un número no es reconocido como teléfono (ej: contiene "@").
- **Session ID inconsistente**: Si `message_data["sender"]` no es normalizado consistentemente (depende de `_parse_whatsapp_payload`), podría cambiar.
- **InMemoryStorage por defecto**: Si `USE_POSTGRES_FOR_MEMORY=false` (o no configurado), la memoria es volátil (pérdida al reiniciar, múltiples workers).
- **Logging insuficiente**: No se loguea el contenido del historial recuperado, solo el count.
- **Prompt no enfatiza uso de historial**: El system prompt no instruye explícitamente al LLM a consultar el historial para evitar repetir preguntas.

---

## Plan de Implementación

### Fase 1: Diagnóstico (sin cambios de código)

**Acciones para el usuario:**

1. Verificar configuración:

   ```bash
   grep USE_POSTGRES_FOR_MEMORY .env
   ```

   Debe ser `true`. Si es `false`, la memoria es InMemory (volátil).

2. Revisar logs en tiempo real durante una conversación:

   ```bash
   ./run.sh logs
   ```

   Buscar:
   - `"Historial cargado"`: fijarse en `message_count`
   - `"Agregando mensaje a memoria"`: que aparezca para cada mensaje
   - `"Mensaje guardado en memoria"` (si usas PostgreSQL)

3. Consultar la base de datos directamente:

   ```sql
   SELECT session_id, type, left(content, 100) as preview, created_at
   FROM langchain_memory
   WHERE session_id = '+34612345678'  -- usar el número normalizado
   ORDER BY created_at;
   ```

   Si no aparecen filas, la memoria no se guarda.
   Si aparecen, entonces se guarda pero no se recupera o no se usa.

4. Verificar que el `session_id` sea idéntico en ambos mensajes:
   - En log de webhook: `session_id_derived`
   - En log del procesamiento del mensaje: `session_id`
     Deben coincidir.

5. Probar con `USE_POSTGRES_FOR_MEMORY=true` si no lo está, y reiniciar.

---

### Fase 2: Mejoras de Código (si el diagnóstico muestra que la memoria funciona pero el agente no la usa)

#### 2.1. Mejorar el System Prompt

Agregar una instrucción explícita para usar el historial:

```python
DEFAULT_SYSTEM_PROMPT = """
Eres Deyy, el asistente virtual de Arcadium...

INSTRUCCIÓN CRÍTICA: Siempre revisa el historial de conversación (chat_history) antes de responder.
Si el usuario ya proporcionó información (como fecha, hora, nombre, tipo de servicio), NO la vuelvas a pedir.
Usa la información ya disponible en el historial para progresar en la tarea.

Ejemplo:
- Usuario: "Quiero agendar una cita para hoy a las 12."
- Tú: "¿Qué servicio necesitas?"
- Usuario: "Empaste."
- Tú (usa historial): "Perfecto, ya tengo: servicio=Empaste, fecha=hoy a las 12. ¿A nombre de quién la agenda?"

Si la información está incompleta, pide solo lo que falte.
""".strip()
```

Ubicación: `agents/deyy_agent.py`, en la constante `DEFAULT_SYSTEM_PROMPT` (alrededor de línea 840).

#### 2.2. Añadir Logging Detallado del Historial

En `deyy_agent.py:process_message`, después de cargar el historial:

```python
if len(history) > 0:
    logger.debug(
        "Historial recuperado",
        session_id=self.session_id,
        message_count=len(history),
        last_messages=[
            {"type": type(msg).__name__, "content": msg.content[:100]}
            for msg in history[-3:]  # últimos 3
        ]
    )
else:
    logger.warning("Historial vacío", session_id=self.session_id)
```

Esto ayudará a confirmar si el historial llega vacío o lleno.

#### 2.3. Asegurar Normalización Consistente del Session ID

El `session_id` debería ser **siempre** el número normalizado. Ya se hace en el webhook.

Pero en `_get_or_create_agent` se usa `session_id` directamente. Si acaso llega un `session_id` con formato no normalizado (ej: "+34 612 345 678"), deberíamos normalizarlo una vez al crear el agente.

Agregar en `_get_or_create_agent` (orchestrator.py):

```python
async def _get_or_create_agent(self, session_id: str) -> Any:
    # Normalizar session_id para consistencia
    if "@" not in session_id and session_id.replace("+", "").isdigit():
        try:
            normalized_session_id = normalize_phone(session_id)
        except ValueError:
            normalized_session_id = session_id
    else:
        normalized_session_id = session_id

    if normalized_session_id not in self._agents:
        ...
        agent = DeyyAgent(session_id=normalized_session_id, ...)
        self._agents[normalized_session_id] = agent
    return self._agents[normalized_session_id]
```

Esto garantiza que el cache de agentes use claves normalizadas.

#### 2.4. Forzar Uso de PostgreSQL (recomendación)

En `.env`:

```
USE_POSTGRES_FOR_MEMORY=true
```

Y asegurar que la DB tenga la tabla `langchain_memory` (migración 001).

---

### Fase 3: Si Persiste el Problema - Agente de Interpretación de Historial (Alternativa)

Si después de las mejoras el agente sigue sin usar el historial, implementar un **pre-procesador** que genere un resumen del historial y lo inyecte en el prompt.

Implementación:

1. Crear `agents/history_interpreter.py` con función `async def summarize_history(history: List[BaseMessage]) -> str`.
2. En `process_message`:
   - Obtener historial
   - Llamar al intérprete para generar resumen (ej: "Usuario quiere agendar cita de Empaste hoy a las 12, aún no ha dado nombre.")
   - Pasar ese resumen como parte del input o en una sección especial del prompt.

Esto es una alternativa más pesada, pero puede ayudar si el LLM no sigue el historial por limitación de contexto o instrucción.

---

## Pasos Concretos a Ejecutar

1. **Primero**: Diagnosticar con los pasos de Fase 1 (sin código). El usuario debe verificar:
   - `USE_POSTGRES_FOR_MEMORY`
   - Logs de message_count
   - Contenido de tabla `langchain_memory`

2. **Si la memoria funciona** (aparecen datos en DB y message_count>0), entonces el problema es del agente (no usa historial). Aplicar cambios de **Fase 2.1 y 2.2**.

3. **Si la memoria NO funciona** (DB vacía o message_count=0), entonces:
   - Verificar que el session_id sea el mismo en ambos mensajes.
   - Aplicar cambio de **Fase 2.3** (normalización en cache).
   - Cambiar a PostgreSQL si usa InMemory.

4. **Probar** después de cada cambio enviando dos mensajes secuenciales y revisando el endpoint `/api/history/{session_id}`.

---

## Archivos a Modificar (si se requiere)

- `agents/deyy_agent.py`: mejorar system prompt y logging.
- `core/orchestrator.py`: normalizar session_id en `_get_or_create_agent`.
- `.env`: cambiar configuración a PostgreSQL.

---

## Verificación

- Endpoint `/api/history/{session_id}` debe devolver mensajes completos.
- Logs deben mostrar `Historial cargado` con message_count > 0 en el segundo mensaje.
- El agente debe dejar de preguntar datos ya proporcionados.

---

## Preguntas al Usuario

1. ¿Qué valor tiene `USE_POSTGRES_FOR_MEMORY` en tu `.env`?
2. ¿Estás viendo los logs del sistema cuando pruebas? ¿Qué `message_count` aparece?
3. ¿Puedes ejecutar la query SQL sobre `langchain_memory` para ver si hay datos?
4. ¿El servidor se reinicia entre mensajes? (podría limpiar InMemory)
5. ¿Tienes múltiples workers/instancias corriendo?

Con esta información podremos afinar la solución.
