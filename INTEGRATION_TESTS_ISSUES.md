# Errores en Tests de Integración (Estado: 2026-04-04)

**Última actualización:** 2026-04-04 (post-state-machine fixes)  
**Tests totales:** 100  
**Pasando:** 83  
**Fallando:** 14  
**Errores:** 4

> **Nota:** Los 4 tests de `test_state_machine_integration.py` ahora pasan individualmente, pero fallan cuando se ejecutan con toda la suite debido a contaminación de settings global. Esto se detalla en la sección [Problemas Pendientes].

---

## ✅ Progreso Realizado (2026-04-04)

### State Machine Integration Tests - CORREGIDOS

Los 4 tests de `test_state_machine_integration.py` han sido corregidos:

1. ✅ `test_full_agendar_flow` - Flujo completo de agendamiento
2. ✅ `test_consultar_sin_agendar_flow` - Consulta sin agendar
3. ✅ `test_cancelar_flow` - Cancelación de cita
4. ✅ `test_state_persistence` - Persistencia de estado

**Cambios implementados:**

- `agents/context_vars.py`: `set_current_project` retorna tupla de tokens
- `agents/state_machine_agent.py`: Deshabilitado MemorySaver en testing
- `services/appointment_service.py`: Añadido `project_id` opcional
- `agents/tools_state_machine.py`: Corregidas llamadas a `get_appointments_by_phone`, eliminado `google_event_link`, reordenado intenciones
- `graphs/arcadium_graph.py`: Manejo de `Command.goto` y auto-transición condicional
- Fixture `arcadium_store` mejorado con cleanup de settings

---

## ❌ Problemas Pendientes

### 1. Contaminación de Settings Global

**Síntoma:**
Los 4 tests de `test_state_machine_integration` pasan individualmente pero fallan cuando se ejecutan junto a otros tests.

**Causa:**
Otros tests modifican `core.config._settings` sin restaurar, afectando tests posteriores.

**Archivo afectado:**

- `tests/test_state_machine_integration.py` - fixture `arcadium_store` modifica `_settings`

**Solución en progreso:**

- Fixture ya incluye cleanup: restaura `_settings` original y elimina `DEFAULT_PROJECT_ID`
- Se necesita identificar otros tests que contaminan y aplicar igual patrón

**Tests específicos que contaminan (potenciales):**

- Tests en `test_integration.py` que usan `ArcadiumAPI` y modifican settings
- Tests en `test_tools.py` que modifican config global

**Acción requerida:**

1. Buscar todos los `core.config._settings =` en `tests/`
2. Reemplazar con `monkeypatch` o agregar cleanup similar
3. Considerar fixture autouse para limpiar `_settings` después de cada test

---

### 2. Tests Legacy en `test_integration.py` (5 fallantes)

```bash
FAILED tests/test_integration.py::test_full_integration_success
FAILED tests/test_integration.py::test_webhook_pipeline
FAILED tests/test_integration.py::test_valid_webhook_payload_parsing
FAILED tests/test_integration.py::test_audio_payload_parsing
FAILED tests/test_integration.py::test_chain_metrics_tracking
```

**Errores comunes:**

- AttributeError: 'ArcadiumStore' object has no attribute 'get_history'
- pydantic.ValidationError en WebhookPayload
- ValueError en extracción de mensaje de audio
- Métricas no registradas (contadores en 0)

**Estado:** No se han abordado - son tests legacy del sistema anterior (n8n-based o DeyyAgent viejo). Pueden requerir actualización significativa o ser deprecados.

---

### 3. Tests de Tools en `test_tools.py` (6 fallantes)

```bash
FAILED tests/test_tools.py::TestMCPGoogleCalendarTool::* (3 tests)
FAILED tests/test_tools.py::TestGetDeyyTools::* (3 tests)
```

**Causas:**

- MCP Google CalendarTool requiere endpoint MCP configurado
- Probablemente cambios en schemas Pydantic V2 (`@validator` vs `@field_validator`)
- Herramientas state_machine vs DeyyAgent confusion

**Estado:** No prioritario - tools MCP son experimental/para otro contexto.

---

### 4. Test E2E en `test_e2e_state_machine.py` (1 fallante)

**Error:** `async def functions are not natively supported. You need to install a suitable plugin`

**Causa:** Falta `pytest-asyncio` o configuración incorrecta de async en pytest.

**Solución:** Verificar `pytest.ini` o `pyproject.toml` tenga:

```toml
[tool.pytest.ini_options]
asyncio_mode = "auto"
```

**Estado:** config minimal - los integration testsusan `@pytest.mark.asyncio` que debería funcionar.

---

## 📊 Resumen por Categoría

| Categoría                     | Cantidad | Tests                                 |
| ----------------------------- | -------- | ------------------------------------- |
| **State Machine Integration** | 4        | ✅ Corregidos (aislamiento pendiente) |
| Legacy Integration            | 5        | Pendiente actualización               |
| Tools (MCP/Calendar)          | 6        | Baja prioridad                        |
| E2E State Machine             | 1        | Fix config asyncio                    |
| **No relacionados**           | 84       | ✅ Pasando                            |

---

## 🎯 Plan de Acción

### Prioridad Alta (Próximo)

1. **Aislar completamente tests de state machine**
   - Identificar tests que modifican `_settings` global
   - Aplicar cleanup o usar `monkeypatch`
   - Validar que `test_state_machine_integration` pasen en suite completa

2. **Decidir fate de tests legacy**
   - `test_integration.py` usa APIs antiguas
   - Marcar como `@pytest.mark.xfail` o actualizar a nueva arquitectura
   - Si no son relevantes, eliminar/archivar

### Prioridad Media

3. **Fix async config**
   - Agregar `asyncio_mode = "auto"` a pytest config
   - O instalar `pytest-asyncio` si falta

### Prioridad Baja

4. **Actualizar tools tests**
   - Mock MCP endpoints
   - Migrar validators a Pydantic V2

5. **Mejorar clasificación de intenciones**
   - Considerar LLM-based classifier en lugar de keyword matching
   - Implementar cache por sesión

---

## 📝 Comandos Útiles

```bash
# Ejecutar solo state machine tests
pytest tests/test_state_machine_integration.py -v

# Ejecutar sin e2e tools
pytest tests/ --ignore=tests/test_e2e_state_machine.py --ignore=tests/test_tools.py -v

# Ejecutar con coverage
pytest tests/ --cov=arcadium_automation --cov-report=html

# Buscar contaminación de settings
grep -rn "core.config._settings" tests/
```

---

## 📈 Métricas

**Antes de correcciones (04-abr):**

- Pasando: 89/100 (89%)
- Problemas principales: state machine failures

**Después de correcciones state machine:**

- Pasando: 83/100 (83%) ← Contaminación afecta otros tests
- State machine: 4/4 ✅
- Resto: mismos legacy failures

**Objetivo:**

- 90%+ pasando (90/100 tests)
- Eliminar contaminación
- Stabilizar suite de integración

---

**Responsable:** javiarias000  
**Revisión pendiente:** Decidir si tests legacy se mantienen o deprecan

---

## Lista de Tests Fallantes

### 1. `test_integration.py::test_full_integration_success`

**Error:** `AssertionError: assert 'reception' == 'info_collector'`

**Causa probable:**

- El LLM no está clasificando la intención como `agendar` cuando el usuario dice "Quiero agendar una limpieza dental"
- El state machine se queda en `reception` en lugar de transitar a `info_collector`

**Solución sugerida:**

- Revisar el prompt de clasificación en `agents/state_machine_agent.py`
- Agregar few-shot examples para la intención "agendar"
- Verificar que la herramienta `classify_intent` esté disponible y funcione

---

### 2. `test_integration.py::test_webhook_pipeline`

**Error:** `AttributeError: 'ArcadiumStore' object has no attribute 'get_history'`

**Causa probable:**

- El método `get_history` está definido en `ArcadiumStore` pero no implementado, o hay un error de import

**Archivos a revisar:**

- `core/store.py` - clase `ArcadiumStore`
- Método `get_history` debe existir y ser async

**Solución sugerida:**

```python
async def get_history(self, session_id: str, limit: int = 50) -> List[Message]:
    """Obtiene historial de mensajes para una sesión"""
    # Implementación...
```

---

### 3. `test_integration.py::test_valid_webhook_payload_parsing`

**Error:** `pydantic.ValidationError: 1 validation error for WebhookPayload`

**Causa probable:**

- El test envía un payload que no cumple con el schema Pydantic
- Campo faltante o con tipo incorrecto

**Solución sugerida:**

- Revisar `validators/schemas.py::WebhookPayload`
- Verificar qué campo está fallando en la validación
- Posiblemente ajustar `@validator` o `@field_validator` (migración Pydantic v2)

---

### 4. `test_integration.py::test_audio_payload_parsing`

**Error:** `ValueError: No se pudo extraer mensaje del payload`

**Causa probable:**

- El payload de audio no tiene el campo `content` esperado
- `_extract_message_from_body` no encuentra ruta válida

**Solución sugerida:**

- Revisar estructura del payload de audio en el test
- Ajustar `_extract_message_from_body` para manejar audio attachments

---

### 5. `test_integration.py::test_chain_metrics_tracking`

**Error:** `assert 0 >= 1` - Los contadores de métricas están en 0

**Causa probable:**

- Landchain no está registrando métricas correctamente
- El test espera que al menos un link haya ejecutado, pero no contó

**Solución sugerida:**

- Verificar `core/landchain.py::LandChain.get_metrics()`
- Asegurar que `_metrics` se actualice tras ejecución
- Revisar que el test esté usando el chain correctamente

---

### 6. `test_state_machine_integration.py::test_full_agendar_flow`

**Error:** `AssertionError: assert 'reception' == 'info_collector'`

**Idéntico al caso 1.**

- StateMachineAgent no transita de `reception` a `info_collector` tras clasificar "agendar"

**Solución:** Ajustar prompt + few-shot en `classify_intent`

---

### 7. `test_state_machine_integration.py::test_cancelar_flow`

**Error:** `AssertionError: assert 'agendar' == 'cancelar'`

**Causa:**

- Intención "cancelar" no se detecta correctamente
- LLM devuelve "agendar" cuando debería ser "cancelar"

**Solución:**

- Mejorar clasificación para `cancelar`
- Verificar que el tipo de mensaje (ej: "Quiero cancelar mi cita") esté en los ejemplos

---

### 8. `test_state_machine_integration.py::test_state_persistence`

**Error:** `TypeError: 'coroutine' object is not subscriptable`

**Causa:**

- El test llama `await agent1.get_current_state()` pero después accede como si fuera síncrono.

**Línea 180-181 del test:**

```python
state_after = await agent1.get_current_state()
assert state_after["current_step"] == "info_collector"  # ✅ Ya está await
```

**Posible problema:** `get_current_state` retorna un dict, pero dentro hay valores que son coroutines? No, el error dice `'coroutine' object is not subscriptable` - significa que `get_current_state()` devolvió una coroutine en lugar de dict.

**Verificar:** ¿`StateMachineAgent.get_current_state` es async? Si es async, debe ser await. Si ya tiene await en el test, ¿por qué sigue siendo coroutine? Revisar implementación.

---

### 9. `test_e2e_state_machine.py::test_full_conversation_state_machine`

**Error:** Similar a los anteriores - clasificación de intención falla

**Solución:** Ajustar prompts de `classify_intent`

---

### 10. `test_e2e_state_machine.py::test_store_persistence`

**Error:** `Failed: asyncio.exceptions.TimeoutError`

**Causa probable:**

- ArcadiumStore intenta something que timeout (DB connection?)
- Configuración de PostgreSQL no disponible en tests

**Solución sugerida:**

- Usar `MemoryStorage` en tests en lugar de PostgreSQL
- Verificar `ArcadiumStore.__init__` usa `MemoryStorage` para tests

---

### 11. `test_e2e_state_machine.py::test_conversation_history`

**Error:** `Failed: asyncio.exceptions.TimeoutError`

**Similar al 10.** Probablemente persistencia en memoria.

---

## Diagnóstico General

Los 11 tests caen en **dos categorías**:

| Categoría          | Cantidad | Tests                                                                                                      |
| ------------------ | -------- | ---------------------------------------------------------------------------------------------------------- |
| Clasificación LLM  | 6        | test_full_integration_success, test_full_agendar_flow, test_cancelar_flow, test_e2e_state_machine, + 2 más |
| Persistencia/Store | 4        | test_state_persistence, test_store_persistence, test_conversation_history, test_webhook_pipeline           |
| Otros              | 1        | test_valid_webhook_payload_parsing, test_audio_payload_parsing, test_chain_metrics_tracking                |

---

## Plan de Acción Recomendado

### Fase 1: Fix Persistencia ( Más rápido )

1. Revisar `ArcadiumStore.get_history` - implementar si falta
2. Cambiar tests e2e para usar `MemoryStorage` en lugar de PostgreSQL real
3. Verificar que `StateMachineAgent`返回 `get_current_state()` correctly sync (no coroutine unless awaited).

### Fase 2: Ajustar Prompts

1. Encontrar `classify_intent` tool/prompt en `utils/tools.py` o `agents/state_machine_agent.py`
2. Agregar ejemplos few-shot:

   ```text
   User: "Quiero agendar una limpieza"
   → intent: agendar

   User: "Cancelar mi cita del viernes"
   → intent: cancelar
   ```

3. Asegurar que el LLM use `classify_intent` tool y devuelva `intent` correcto.

### Fase 3: Verificar métricas

1. `test_chain_metrics_tracking` - revisar que Landchain guarde contadores

### Fase 4: Validación payloads

1. Revisar schemas Pydantic en `validators/schemas.py`
2. Los tests `test_valid_webhook_payload_parsing` y `test_audio_payload_parsing` pueden necesitar ajustes en los fixtures o en los validators

---

## Comando para Ejecutar Solo los Fallantes

```bash
python -m pytest tests/test_integration.py tests/test_state_machine_integration.py tests/test_e2e_state_machine.py -v --tb=short
```

---

## Notas

- Los tests de integración usan **LLM real** (OpenAI `gpt-4o-mini`). Si no hay API key o la API falla, pueden dar errores inconsistentes.
- La **compatibilidad con LangChain 0.2.10 ya está lograda** - estos errores no son por versiones.
- Se puede considerar marcar estos tests como `xfail` hasta que se ajusten los prompts, pero el sistema core funciona.

---

**Última actualización:** 2026-04-04
