# Plan de Migración: nodes_backup.py → módulos separados

## Estado Actual

✓ Estructura base creada:
- `src/nodes/` es un package
- `src/nodes/__init__.py` re-exporta desde `nodes_backup.py`
- Tests pasan (60/60)
- graph.py sin cambios necesarios

## Fase 2: Migración de módulos

Completar en este orden (cada uno es independiente):

### 1. `_helpers.py` → 160 líneas
Funciones helper usadas por múltiples nodos:
```python
_resolve_calendar_service, _last_human_text, _safe_node,
_normalize_phone, _phone_in_text, _name_in_text, _service_in_text,
_parse_event_start, _event_to_dict, _extract_patient_name_from_description,
_no_appointment_found, _compute_slots_available,
_build_llm_context, _format_datetime_readable, _format_slots
```

**Pasos:**
1. Copiar líneas 63-1082 de `nodes_backup.py` a `src/nodes/_helpers.py`
2. Copiar imports (líneas 21-56) a `_helpers.py`
3. En `__init__.py`: cambiar `from src.nodes_backup import` por `from src.nodes._helpers import`

### 2. `cancel.py` → 90 líneas
```python
node_cancel_appointment + helpers específicos
```

**Pasos:**
1. Extraer línea 911-1003 de `nodes_backup.py`
2. Crear `src/nodes/cancel.py` con imports necesarios
3. Actualizar `__init__.py`

### 3. `reschedule.py` → 160 líneas
```python
node_reschedule_appointment (1529-1677)
node_prepare_modification (1503-1525)
```

### 4. `booking.py` → 170 líneas
```python
node_book_appointment (743-896)
node_detect_confirmation (654-720)
node_validate_and_confirm (722-740)
```

### 5. `availability.py` → 330 líneas
```python
node_check_availability (387-574)
node_match_closest_slot (577-650)
node_check_missing (354-363)
node_adjust_weekend (366-384)
node_check_existing_appointment (1084-1341)
node_lookup_appointment (1404-1500)
```

### 6. `flow.py` → 200 líneas
```python
node_entry (194-307)
node_route_intent (310-351)
node_save_state (1680-1726)
```

### 7. `intent.py` → 110 líneas
```python
node_extract_intent (1728-1747)
node_extract_data (1749-1856)
```

### 8. `response.py` → 600 líneas
```python
node_generate_response (1858-1880)
node_generate_response_with_tools (2011-2592)
node_get_appointment_history (2594-2652)
node_execute_memory_tools (2654-2770)
edge_after_generate_response (2772-2801)
```

---

## Estructura de cada archivo

```python
"""
Descripción del módulo.
"""

from __future__ import annotations

# Imports estándar
import ...

# Imports del proyecto
from src.state import ArcadiumState
from src.nodes._helpers import _resolve_calendar_service, ...

# Logger específico
logger = structlog.get_logger("langgraph.nodes.FILENAME")

# Función(es) específicas del módulo
async def node_xxxxx(...):
    pass
```

---

## Testing

Después de migrar cada módulo:

```bash
# Test imports
python -c "from src.nodes.cancel import node_cancel_appointment; print('✓')"

# Test suite completo
python -m pytest tests/test_booking_nodes.py -q
```

---

## Rollback

Si algo se rompe:
```bash
git checkout HEAD src/nodes/
# o simplemente elimina __init__.py y los archivos nuevos:
rm src/nodes/*.py
# y el sistema volverá a nodes_backup.py automáticamente
```

---

## Notas

- **nodes_backup.py** se puede eliminar DESPUÉS de migrar todo
- Actualizar **nodes/__init__.py** con imports de los nuevos módulos
- Mantener compatibilidad: `from src.nodes import node_book_appointment` debe seguir funcionando
