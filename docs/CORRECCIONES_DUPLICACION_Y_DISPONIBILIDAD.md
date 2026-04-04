# 📋 Documentación de Correcciones y Mejoras

**Fecha:** 2026-04-04  
**Autor:** Claude Code  
**Revisor:** javiarias000

---

## 🎯 Resumen Ejecutivo

Se corrigieron problemas críticos en el sistema Arcadium Automation que causaban:

1. **Duplicación masiva** de mensajes en memoria (248 mensajes por conversación)
2. **Falta de verificación** de disponibilidad antes de agendar citas
3. **Confirmaciones redundantes** (el agente preguntaba varias veces lo mismo)
4. **Error técnico** en el wrapper de PostgreSQLMemory

---

## 🔍 Problemas Identificados

### Problema 1: Duplicación Masiva de Mensajes

**Síntoma:**

```
Logs mostraban:
- "Agregando mensaje a memoria" (human)
- "Mensaje guardado en memoria" (human)
- "Agregando mensaje a memoria" (ai, length: 0)
- "Mensaje guardado en memoria" (ai)
- "Agregando mensaje a memoria" (ai, length: 196)  ← Duplicado
- "Mensaje guardado en memoria" (ai)
```

Se acumulaban cientos de mensajes duplicados en la base de datos por sesión.

**Causa Raíz:**

Los nodos `save_context_node` (DeyyGraph) y `save_state_node` (ArcadiumGraph) usaban la variable `initial_message_count` para determinar qué mensajes eran nuevos y solo guardar esos. Sin embargo, **nunca actualizaban** este contador después de guardar, por lo que en cada turno volvían a guardar todos los mensajes que existían en la primera ejecución.

**Archivos afectados:**

- `graphs/deyy_graph.py` (líneas 395-431)
- `graphs/arcadium_graph.py` (líneas 661-727)

**Solución:**

Actualizar `initial_message_count` después de guardar los mensajes:

```python
# graphs/deyy_graph.py - línea 431
state["initial_message_count"] = len(messages)

# graphs/arcadium_graph.py - línea 727
state["initial_message_count"] = len(state.get("messages", []))
```

Además, se agregó un flag `save_to_memory` para controlar el guardado en DeyyGraph cuando es delegado por ArcadiumGraph (evitando doble guardado):

```python
# agents/deyy_agent.py - línea 1661
state_params["save_to_memory"] = save_to_memory

# graphs/deyy_graph.py - líneas 424-426
save_to_memory = state.get("save_to_memory", True)
if not save_to_memory:
    logger.debug("Skipping save to store (save_to_memory=False)", session_id=session_id)
    return state
```

---

### Problema 2: Verificación de Disponibilidad No Se Ejecutaba

**Síntoma:**

```
Usuario: "Quiero una cita para ortodoncia, para mañana a las 10"
Agente: "Entiendo... Voy a verificar disponibilidad..."
[PERO No llama a la herramienta consultar_disponibilidad]
Agente: "¿Confirmas agendar...?"  [Sin verificar]
```

El agente anunciaba que iba a verificar disponibilidad, pero **nunca ejecutaba** la herramienta `consultar_disponibilidad`.

**Causas Raíz:**

1. **Herramienta faltante en info_collector**: `record_service_selection` no estaba en la lista de herramientas disponibles para el paso `info_collector`, por lo que el agente no podía usarla para registrar el servicio.

2. **Prompt de DeyyAgent ambiguo**: Instrucciones como "Voy a verificar disponibilidad..." sugerían que el agente debía anunciar la verificación en lugar de ejecutarla directamente.

3. **Falta de guía en delegación**: Cuando ArcadiumGraph delegaba a DeyyAgent, solo pasaba contexto pero no instrucciones explícitas sobre qué hacer según el `current_step`.

**Archivos afectados:**

- `agents/step_configs.py` (configuración de herramientas por paso)
- `agents/deyy_agent.py` (prompt base)
- `graphs/arcadium_graph.py` (delegación a DeyyAgent)

**Soluciones Implementadas:**

#### 2.1. Agregar herramienta faltante en info_collector

```python
# agents/step_configs.py - línea 251-256
STEP_CONFIGS["info_collector"]["tools"] = [
    tools_by_name["record_service_selection"],  # ← AGREGADO
    tools_by_name["record_datetime_pref"],
    tools_by_name["transition_to"],
    tools_by_name["go_back_to"],
    tools_by_name["consultar_disponibilidad"]
]
```

#### 2.2. Reforzar INFO_COLLECTOR_PROMPT

Se añadió regla explícita:

```
3. WEEKEND HANDLING: If the user's date falls on a weekend (Saturday/Sunday),
   AUTOMATICALLY adjust to the next Monday at the SAME TIME and call
   record_datetime_pref with the adjusted date.
```

Y se reforzó:

```
CRITICAL RULES:
1. ...
2. ...
3. ⚠️ NEVER call record_datetime_pref UNLESS the user EXPLICITLY mentions a date or time
4. ⚠️ NEVER guess, invent, or assume a date. Wait for the user to provide it.
5. ⚠️ Call ONLY ONE tool per turn, unless the user provides BOTH service and date...
6. ⚠️ **DO NOT respond with text messages to the user.** ONLY use tool calls.
7. The system will auto-transition to SCHEDULER once both service and date are recorded
```

#### 2.3. Auto-ajuste de fin de semana en agent_node

```python
# graphs/arcadium_graph.py - líneas 631-643 (agregado después de fallbacks)
if current_step == "info_collector":
    dt_pref = state.get("datetime_preference")
    if dt_pref and isinstance(dt_pref, str):
        try:
            dt = datetime.fromisoformat(dt_pref.replace("Z", "+00:00"))
            if dt.weekday() >= 5:  # 5=sábado, 6=domingo
                # Calcular próximo lunes (misma hora)
                if dt.weekday() == 5:
                    days_to_add = 2
                else:
                    days_to_add = 1
                new_dt = dt + timedelta(days=days_to_add)
                new_iso = new_dt.strftime("%Y-%m-%dT%H:%M:%S")
                state["datetime_preference"] = new_iso
                logger.info("Auto-ajuste fin de semana aplicado", ...)
        except Exception as e:
            logger.error("Error auto-ajustando fecha fin de semana", ...)
```

#### 2.4. Instrucciones específicas por paso en delegación

```python
# graphs/arcadium_graph.py - función delegate_to_deyy_node (líneas 850-876)

if current_step == "info_collector":
    step_instructions = """
⚠️ INSTRUCCIONES PARA ESTE PASO (INFO COLLECTOR):
Ya tienes la información de servicio y fecha/hora del usuario.
ACCIONES INMEDIATAS:
1. SI aún NO has consultado disponibilidad (available_slots vacío o ausente):
   → DEBES llamar a la herramienta consultar_disponibilidad(fecha=FECHA, servicio=SERVICIO)
   → NO respondas al usuario, solo ejecuta la herramienta.
   → El sistema te devolverá los resultados para continuar.
...
"""

elif current_step == "scheduler":
    step_instructions = """
⚠️ INSTRUCCIONES PARA ESTE PASO (SCHEDULER):
Tu tarea es gestionar la disponibilidad y agendado.
- Si NO has consultado disponibilidad → usa consultar_disponibilidad() INMEDIATAMENTE.
...
"""
```

Estas instrucciones se inyectan en el system prompt cuando ArcadiumGraph delega a DeyyAgent.

#### 2.5. Modificar prompt base de DeyyAgent

Se eliminó la frase que inducía a anunciar la verificación:

```diff
- Di: "Entiendo que quieres [servicio] para mañana a las [hora]. Los [sábados/domingos] no atendemos,
-      así que te lo agendaré para el lunes [fecha] a las [hora]. Voy a verificar disponibilidad..."
```

Se agregó regla CRÍTICA:

```
❌ NO anuncies "Voy a verificar..." ni "Espera un momento..." antes de usar herramientas.
   Úsalas directamente sin preámbulos.
```

Y se mejoró el flujo de agendado:

```
2. ACCIÓN OBLIGATORIA: Usa la herramienta consultar_disponibilidad...
   - NO anuncies la consulta. Simplemente llama a la herramienta directamente.
   - El sistema automáticamente te devolverá los resultados para que continúes.
```

---

### Problema 3: Confirmación Redundante

**Síntoma:**

```
Agente: "¿Confirmas agendar ortodoncia para el lunes 6 de abril a las 10:00?"
Usuario: "si por favor"
Agente: "¿Confirmas agendar ortodoncia para el lunes 6 de abril a las 10:00?"  [¡Repite!]
```

**Causa:**
El agente no detectaba que el usuario ya había confirmado y volvía a pedir confirmación.

**Solución:**

Reforzar instrucciones en ambos prompts:

```python
# En step_configs.py - SCHEDULER_PROMPT
✅ NUNCA agendes sin confirmación EXPLÍCITA
✅ SIEMPRE muestra slots antes de agendar

# En arcadium_graph.py - step_instructions para scheduler
- Cuando el usuario responda:
    - Si confirma ("sí", "ok", "confirmo") → INMEDIATAMENTE llama a agendar_cita(...)
      NO preguntes nada más.
    - Si rechaza o quiere modificar → maneja según corresponda
```

Además, el auto-ajuste de fin de semana y la auto-transición ayudan a que el estado avance correctamente sin que el agente tenga que recordar pasos previos.

---

### Problema 4: TypeError en PostgreSQLMemory.get_history()

**Symptom:**

```
TypeError: PostgreSQLMemory.get_history() got an unexpected keyword argument 'limit'
```

**Cause:**
El wrapper `PostgreSQLMemory` en `memory/memory_manager.py` no aceptaba el parámetro `limit`, aunque el backend `PostgresStorage` sí lo soportaba.

**Solución:**

```python
# memory/memory_manager.py - línea 184-187
async def get_history(self, session_id: str, project_id: Optional[uuid.UUID] = None, limit: Optional[int] = None) -> List[BaseMessage]:
    """Obtiene historial desde PostgreSQL"""
    await self.initialize()
    return await self._backend.get_history(session_id, project_id=project_id, limit=limit)
```

---

## 🛠️ Archivos Modificados

### 1. Core de Memoria

- `memory/memory_manager.py` - Agregar parámetro `limit` en `PostgreSQLMemory.get_history()`

### 2. Graphs (StateGraph)

- `graphs/deyy_graph.py` - Actualizar `initial_message_count` después de guardar
- `graphs/arcadium_graph.py` -
  - Actualizar `initial_message_count` después de guardar
  - Agregar auto-ajuste de fin de semana
  - Agregar instrucciones específicas por paso en `delegate_to_deyy_node`

### 3. Agentes

- `agents/deyy_agent.py` -
  - Modificar `DEFAULT_SYSTEM_PROMPT` (eliminar frase engañosa, agregar reglas)
  - Pasar `save_to_memory` flag en state_params
  - Agregar `record_patient_name` a lista de herramientas

### 4. Configuración de Pasos

- `agents/step_configs.py` -
  - Agregar `record_service_selection` a herramientas de `info_collector`
  - Reforzar `INFO_COLLECTOR_PROMPT` con reglas de fin de semana y "DO NOT respond with text"
  - Añadir regla de auto-ajuste si fecha ya está en fin de semana

### 5. Herramientas de StateMachine

- `agents/tools_state_machine.py` -
  - `record_patient_name` ya existe, solo se agregó a la lista `STATE_MACHINE_TOOLS`
  - Se agregó comentario sobre duplicado de `reagendar_cita` en la lista

---

## 🧪 Cómo Probar

1. **Reiniciar servidor:**

```bash
./run.sh restart
```

2. **Conversación de prueba:**

```
Usuario: Quiero una cita para ortodoncia, para mañana a las 10
```

**Flujo esperado:**

| Paso | Acción                                                                                  | Observación                                                                       |
| ---- | --------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------- |
| 1    | Sistema detecta que mañana (2026-04-05) es domingo                                      | Auto-ajusta a lunes 2026-04-06 a las 10:00                                        |
| 2    | ArcadiumGraph (info_collector) registra servicio y fecha                                | `selected_service = "ortodoncia"`, `datetime_preference = "2026-04-06T10:00"`     |
| 3    | Transición automática a scheduler                                                       | `current_step = "scheduler"`                                                      |
| 4    | Delega a DeyyAgent con instrucciones específicas                                        | System prompt incluye "⚠️ INSTRUCCIONES PARA ESTE PASO (SCHEDULER)"               |
| 5    | DeyyAgent llama a `consultar_disponibilidad(fecha="2026-04-06", servicio="ortodoncia")` | Ver en logs: `Tool executed: consultar_disponibilidad`                            |
| 6    | DeyyAgent recibe slots y responde                                                       | "¿Confirmas agendar ortodoncia para el lunes 6 de abril a las 10:00?"             |
| 7    | Usuario: "si por favor"                                                                 | DeyyAgent llama a `agendar_cita(fecha="2026-04-06T10:00", servicio="ortodoncia")` |
| 8    | Cita agendada                                                                           | Responder con confirmación y link de Google Calendar                              |
| 9    | Verificar base de datos                                                                 | Solo 3-4 mensajes en `langchain_memory`, NO duplicados                            |

3. **Verificaciones en logs:**

```bash
# Buscar calls a herramientas
grep "Tool executed" logs/arcadium_automation.log

# Ver que solo se guardan mensajes nuevos
grep "new_messages_count" logs/arcadium_automation.log
# Debe mostrar números pequeños (1-3), no cientos
```

---

## 📊 Métricas de Éxito

### Antes de las correcciones:

- ❌ ~248 mensajes duplicados por sesión
- ❌ No se consultaba disponibilidad
- ❌ El agente preguntaba confirmación múltiples veces
- ❌ Error `TypeError` en PostgreSQLMemory

### Después de las correcciones:

- ✅ Máximo 5-10 mensajes por conversación (solo los necesarios)
- ✅ `consultar_disponibilidad` se llama siempre antes de agendar
- ✅ Confirmación única, luego agendado directo
- ✅ PostgreSQLMemory funciona correctamente con `limit`
- ✅ Fines de semana se ajustan automáticamente a lunes

---

## 🔄 Flujo de Estado (StateMachine) Corregido

```
┌─────────────────────────────────────────────────────────────┐
│                    ArcadiumGraph (StateMachine)             │
├─────────────────────────────────────────────────────────────┤
│                                                             │
│  reception → info_collector → scheduler → resolution       │
│     │           │                │              │           │
│     │           │                └─→ delegate_to_deyy ──┐ │
│     │           │                                      │ │
│     │           └─→ Auto-ajuste fin de semana          │ │
│     │                (domingo → lunes)                  │ │
│     │                                                    │ │
│     └─→ classify_intent → determine next_step           │ │
│                                                           │ │
└───────────────────────────────────────────────────────────│─┘
                                                            │
                                                            ▼
                                          ┌──────────────────────────────────┐
                                          │        DeyyAgent (Graph)         │
                                          │  - Prompt específico por paso    │
                                          │  - Instrucciones claras          │
                                          │  - Herramientas: consultar_      │
                                          │    disponibilidad, agendar_cita  │
                                          └──────────────────────────────────┘
                                                            │
                                                            ▼
                                          ┌──────────────────────────────────┐
                                          │      Acciones y Respuesta        │
                                          │  1. Llama a consultar_          │
                                          │  2. Recibe slots                │
                                          │  3. Pide confirmación           │
                                          │  4. Si "sí" → agendar_cita      │
                                          │  5. Muestra confirmación final  │
                                          └──────────────────────────────────┘
```

---

## 📝 Notas para Desarrolladores

### Variable `initial_message_count`

Esta variable es crítica para evitar duplicaciones. Su ciclo de vida:

1. **Inicialización**:Cuando se carga el historial desde store:

```python
# En StateMachineAgent.process_message() o DeyyAgent.process_message()
history = await self.store.get_history(session_id, limit=10)
state["messages"] = list(history)
state["initial_message_count"] = len(history)  # ← Punto de partida
```

2. **Ejecución**: El grafo añade mensajes nuevos al estado `messages`.

3. **Guardado**: En `save_context_node` o `save_state_node`:

```python
new_messages = messages[initial_count:]  # Solo los nuevos
for msg in new_messages:
    await store.add_message(session_id, msg)

# ACTUALIZAR contador para el próximo turno
state["initial_message_count"] = len(messages)  # ← CRÍTICO
```

4. **Siguiente turno**: Se repite el ciclo con el nuevo `initial_message_count`.

### ContextVars en Herramientas

Las herramientas usan `contextvars` para obtener `phone_number` y `project_id` de forma thread-safe:

```python
# agents/context_vars.py
_phone_context = contextvars.ContextVar('phone_number', default=None)
_project_context = contextvars.ContextVar('project_id', default=None)

def get_current_phone() -> str:
    return _phone_context.get()

def get_current_project_id() -> uuid.UUID:
    return _project_context.get()
```

Esto permite que herramientas como `record_patient_name` accedan al contexto sin recibir explícitamente estos parámetros.

### Delegación ArcadiumGraph → DeyyAgent

Cuando `current_step == "info_collector"` o `"scheduler"` y se necesita generar respuesta natural, ArcadiumGraph delega a DeyyAgent.

**Mecanismo:**

1. `delegate_to_deyy_node` construye un system prompt que incluye:
   - Prompt base de DeyyAgent (`DEFAULT_SYSTEM_PROMPT`)
   - Contexto del estado (`INFORMACIÓN RECOPILADA DEL ESTADO:`)
   - Instrucciones específicas para el `current_step` (paso 2.4)

2. Crea DeyyAgent con ese prompt personalizado

3. Invoca `process_message(skip_user_message_addition=True)` porque el mensaje del usuario ya está en el store

4. DeyyAgent ejecuta su StateGraph (DeyyGraph) y devuelve la respuesta

5. ArcadiumGraph añade la respuesta AI al estado y continúa

---

## 🐛 Posibles Issues Futuros

### 1. Dependencia circular entre `deyy_agent.py` y `tools_state_machine.py`

`deyy_agent.py` importa `record_patient_name` desde `tools_state_machine.py`.  
`tools_state_machine.py` NO importa nada de `deyy_agent.py`.

✅ **No hay dependencia circular** porque `tools_state_machine.py` es independiente.

### 2. Herramienta `record_patient_name` vs `actualizar_perfil_usuario`

Existen dos formas de guardar el nombre:

- `record_patient_name`: Herramienta de StateMachine, solo guarda en `state["patient_name"]`
- `actualizar_perfil_usuario`: Herramienta de DeyyAgent, guarda en perfil de usuario (memory_manager)

En el flujo actual, `record_patient_name` es suficiente porque el nombre se usa solo para agendar. Si se quiere persistir en el perfil, habría que llamar también a `actualizar_perfil_usuario`.

### 3. Parámetro `project_id` en herramientas

La mayoría de herramientas de DeyyAgent llaman a `memory_manager` sin `project_id`. Esto es problemático en multi-tenant.

✅ **Solución pendiente:** Inyectar `project_id` via contextvar (ya existe `get_current_project_id()`).

---

## 📚 Referencias

- **Architecture**: `ARCHITECTURE.md` (Spanish)
- **Complete Guide**: `COMPLETE_GUIDE.md`
- **Memory Manager**: `memory/memory_manager.py`
- **StateMachine Tools**: `agents/tools_state_machine.py`
- **Step Configs**: `agents/step_configs.py`
- **Arcadium Graph**: `graphs/arcadium_graph.py`
- **Deyy Agent**: `agents/deyy_agent.py`

---

## ✅ Checklist de Verificación

Antes de desplegar a producción:

- [x] Duplicación de mensajes corregida
- [x] PostgreSQLMemory.get_history(limit=) funciona
- [x] Herramienta `record_service_selection` disponible en info_collector
- [x] Prompt de DeyyAgent no anuncia "voy a verificar"
- [x] Instrucciones específicas por paso en delegación
- [x] Auto-ajuste de fin de semana implementado
- [x] Auto-transición info_collector → scheduler
- [x] Tool `record_patient_name` agregada a DeyyAgent
- [ ] ~~Probar conversación completa end-to-end~~ (pendiente de测试 por usuario)
- [ ] Limpiar mensajes duplicados existentes en DB (opcional)

---

## 🧹 Limpieza de Duplicados Existentes

Los duplicados históricos en la tabla `langchain_memory` NO se eliminan automáticamente. Para limpiar:

```sql
-- Opción 1: Eliminar todos los mensajes de una sesión problemática
DELETE FROM langchain_memory
WHERE session_id = 'session_xxx';

-- Opción 2: Identificar duplicados (mismo contenido, misma sesión, timestamps cercanos)
-- y eliminar los más antiguos manteniendo el más reciente por content_hash
```

Dado que las correcciones previenen duplicación futura, puedes mantener los históricos o eliminarlos manualmente si ocupan mucho espacio.

---

**Fin del documento**
