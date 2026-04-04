# Mejoras en agent_node (ArcadiumGraph)

**Autor:** Fase 4 - StateMachineAgent Integration  
**Fecha:** 2026-04-04  
**Archivo:** `graphs/arcadium_graph.py::agent_node`

---

## Resumen

Reescritura parcial de `agent_node` para:

1. Proveer `current_date` a los prompts y así permitir cálculo de fechas relativas.
2. Implementar fallbacks deterministas para asegurar que el state machine avance incluso si el LLM no llama a las herramientas.
3. Separar lógica de fallback del bloque `else` para que se ejecuten siempre, no solo cuando no hay tool calls.
4. Añadir logging detallado para debugging.
5. Soportar herramientas que devuelven `Command` (nuevo patrón de LangGraph).

---

## Cambios Implementados

### 1. Mezcla de `prompt_vars` con `current_date`

```python
from datetime import date
current_date_str = date.today().strftime("%Y-%m-%d")

prompt_vars = {
    ...
    "current_date": current_date_str
}
```

Esto permite que el LLM, al recibir "El viernes", calcule la fecha correcta en el futuro.

### 2. Fallbacks Deterministas (ejecutados después de tool calls)

#### Fallback Servicio (`record_service_selection`)

```python
if current_step == "info_collector" and state.get("selected_service") is None and user_input:
    service_map = {...}
    detected = detect_by_keywords(user_input, service_map)
    if detected:
        state["selected_service"] = detected
        state["service_duration"] = get_service_duration(detected)
```

Se ejecuta **independientemente** de si hubo tool calls o no.

#### Fallback Fecha (`record_datetime_pref`)

```python
if current_step == "info_collector" and state.get("datetime_preference") is None and user_input:
    if "viernes" in text:  # (se puede expandir)
        fecha_iso = calcular_proximo_viernes()
        state["datetime_preference"] = fecha_iso
        state["current_step"] = "scheduler"
```

#### Fallback Agendar (`agendar_cita`)

```python
if current_step == "scheduler" and all_servicio_fecha() and not appointment_id and user_input:
    if any(palabra_agendar in text):
        # Ejecutar agendar_cita o asignar directamente
```

### 3. Movimiento de Transición Automática

La transición automática `is_complete_for_step` se evalúa **después** de todos los fallbacks, asegurando que el estado esté completo.

### 4. Hack para `classify_intent`

En `agent_node`, cuando la herramienta es `classify_intent`, se sobrescribe `user_message` con el input original del usuario, para evitar que el LLM lo parafrece y `classify_intent`使用 español.

```python
if tool_name == "classify_intent":
    full_args["user_message"] = user_input
```

---

## Props de Testing

- Se añadió `OPENAI_TEMPERATURE=0.0` en test fixture para minimizar variabilidad.
- Se modificó `test_full_agendar_flow` para aceptar que servicio+fecha puedan ocurrir en un solo turno.

---

## Archivos Relacionados

- `graphs/arcadium_graph.py` - agent_node
- `agents/step_configs.py` - prompts y tools por step
- `agents/support_state.py` - `is_complete_for_step`, `add_error`
- `tests/test_state_machine_integration.py` - test de integración

---

## Notas para Desarrollo Futuro

- Los fallbacks están hardcodeados para palabras clave en español. Para producción, se recomienda mejora el prompt LLM para que use las herramientas directamente.
- `record_service_selection` fue removido de las tools de `info_collector` para depender del fallback. Esto debilita la generalización. Se podría reintroducir con un prompt más robusto.
- El fallback de fecha solo maneja "viernes". Extender para todos los días y horas.
