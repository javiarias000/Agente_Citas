# Bitácora de Errores y Soluciones: Flujo de Agendamiento

Este documento registra los errores críticos encontrados en el flujo de agendamiento y las soluciones aplicadas para garantizar la fiabilidad del sistema.

## 1. El Problema del "Súper Match" (Alucinación de Confirmación)
**Síntoma:** El agente confirmaba la cita al usuario ("Su cita ha sido agendada") a pesar de que el sistema nunca había ejecutado la reserva en el calendario.

**Causa Raíz:** 
El `edge_after_check_availability` fallaba al comparar la fecha solicitada con los slots disponibles debido a diferencias sutiles en el formato de los strings (ISO vs UTC offsets), enviando el flujo a `generate_response`. El LLM, al ver que el slot *sí* estaba disponible en el estado, asumía que la cita estaba confirmada.

**Solución:**
- Creación de `utils/date_utils.py` con una comparación granular (Año, Mes, Día, Hora, Minuto) ignorando milisegundos.
- Forzado determinista en el Edge: Si hay match exacto, el flujo DEBE ir a `book_appointment`, saltándose el LLM.

## 2. El Bloqueo del Human-in-the-Loop (HITL)
**Síntoma:** El sistema detectaba el match y enviaba al nodo de reserva, pero la cita nunca se creaba y el agente respondía que no había disponibilidad.

**Causa Raíz:**
En `src/graph.py`, el nodo `book_appointment` estaba incluido en `interrupt_before`. Esto suspendía la ejecución del grafo justo antes de la reserva, esperando una aprobación manual que nunca ocurría.

**Solución:**
- Eliminación de `book_appointment`, `cancel_appointment` y `reschedule_appointment` de la lista de interrupciones en la compilación del grafo.

## 3. Contradicciones por Estado de Disponibilidad
**Síntoma:** Tras crear la cita exitosamente, el LLM respondía: "Lamentablemente, no hay disponibilidad para el lunes a las 10:00", a pesar de haber acabado de crear la cita.

**Causa Raíz:**
Al crear la cita, el slot desaparece de `available_slots`. El LLM veía la lista vacía y concluía que no había disponibilidad, ignorando que el motivo era que él mismo había reservado el slot.

**Solución:**
- Implementación de la **Verdad Absoluta** en `src/nodes.py`. Si existe un `google_event_id`, se inserta una instrucción dominante que anula cualquier análisis de disponibilidad y obliga al LLM a confirmar la cita.

## 4. Alucinaciones de Citas Inexistentes (Memoria Residual)
**Síntoma:** Cuando un usuario intentaba reagendar una cita que no existía, el LLM inventaba una cita (ej. "a las 11:00") basándose en la intención del usuario o el historial.

**Causa Raíz:**
El LLM priorizaba la coherencia del diálogo sobre la realidad del sistema cuando el `intent` era `reagendar` pero no había citas en el calendario.

**Solución:**
- Implementación de una **Guardia de Verdad Global**. Si `cal_found` es `False`, se inserta una alerta crítica prohibiendo mencionar cualquier horario previo o cita programada, calificando cualquier dato contrario como "ALUCINACIÓN".

## 5. Ignorancia de la Verdad por Cambio de Intent
**Síntoma:** El sistema creaba la cita, pero el LLM seguía diciendo que no había disponibilidad porque el `intent` había cambiado a `"otro"`, desactivando las guardias de seguridad.

**Causa Raíz:**
Las guardias de seguridad estaban condicionadas al `intent` (`agendar`, `reagendar`, `cancelar`).

**Solución:**
- Desvinculación de la Verdad Absoluta del intent. Ahora, si hay un `google_event_id` y `confirmation_sent` es `True`, la confirmación es obligatoria independientemente del intent detectado.
