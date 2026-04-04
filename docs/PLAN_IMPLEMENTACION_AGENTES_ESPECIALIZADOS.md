# 🎯 Plan de Implementación: Agentes Especializados

**Objetivo**: Migrar de DeyyAgent monolítico a arquitectura de agentes especializados orquestados.

**Estado actual (2026-04-04)**:

- ✅ `BaseSpecializedAgent` creado
- ✅ `RouterAgent` creado (clasificación por keywords)
- ✅ `AppointmentAgent` creado
- ✅ `appointment_graph.py` creado (state machine completo)
- ⚠️ Orchestrator aún usa DeyyAgent por defecto

---

## 📋 Fase 1: Actualizar Orchestrator (URGENTE)

### Tarea 1.1: Modificar selector de agente

**Archivo**: `core/orchestrator.py`

**Línea**: ~926 (método `_get_or_create_agent`)

**Cambio**:

```python
# ANTES:
if self.settings.ENABLE_STATE_MACHINE:
    from agents.state_machine_agent import StateMachineAgent
    agent = StateMachineAgent(...)

# DESPUÉS:
if self.settings.ENABLE_STATE_MACHINE:
    # Usar nueva arquitectura de agentes especializados
    from agents.router_agent import RouterAgent
    agent = RouterAgent(
        session_id=session_id,
        store=self.store,
        project_id=project_id,
        project_config=project_config,
        whatsapp_service=self.whatsapp_service,
        verbose=self.settings.AGENT_VERBOSE
    )
```

**Verificación**:

```bash
# 1. Detener servidor
./run.sh stop

# 2. Modificar código

# 3. Reiniciar
./run.sh start

# 4. Probar con test simple
curl -X POST http://localhost:8000/webhook/test \
  -H "Content-Type: application/json" \
  -d '{"message": "Quiero una cita para mañana a las 10", "session_id": "test1"}'
```

**Respuesta esperada**:

1. Router clasifica como "agendar"
2. AppointmentAgent procesa
3. Flujo: pide nombre → usuario dice "Juan Pérez" → consulta disponibilidad → pregunta confirmación → usuario dice "sí" → agenda directamente

**Sin bucle**: No debe volver a preguntar disponibilidad después de "sí".

---

### Tarea 1.2: Añadir variable de entorno AGENT_ARCHITECTURE

**Archivo**: `core/config.py`

**Línea**: añadir en clase `Settings`

```python
class Settings(BaseSettings):
    # ... existentes ...

    # Nueva arquitectura de agentes
    AGENT_ARCHITECTURE: Literal["specialized", "monolithic"] = "specialized"
```

**Uso en orchestrator**:

```python
if self.settings.AGENT_ARCHITECTURE == "specialized":
    from agents.router_agent import RouterAgent
    agent = RouterAgent(...)
else:
    # Fallback a DeyyAgent monolítico
    from agents.deyy_agent import DeyyAgent
    agent = DeyyAgent(...)
```

**Ventaja**: Rollback instantáneo cambiando `.env`:

```bash
# Si falla, cambiar a legacy:
AGENT_ARCHITECTURE=monolithic
./run.sh restart
```

---

## 📋 Fase 2: Robustecer RouterAgent

### Tarea 2.1: Mejorar clasificación con LLM (opcional pero recomendado)

**Problema**: Keywords simples pueden fallar con frases ambiguas.

**Solución**: Añadir tool `classify_intent` que use LLM para clasificación cuando score < 2.

**Archivo**: `agents/router_agent.py`

**Implementar**:

```python
async def _classify_intent(self, message: str) -> str:
    """
    Clasificación en dos fases:
    1. Keywords (rápido, barato)
    2. LLM si score baja (para casos ambiguos)
    """
    # Fase 1: Keywords
    scores = self._keyword_scores(message)
    max_intent = max(scores, key=scores.get)
    max_score = scores[max_intent]

    if max_score >= 2:
        return max_intent

    # Fase 2: LLM para casos ambiguos
    intent = await self._llm_classify(message)
    return intent

async def _llm_classify(self, message: str) -> str:
    """Usa LLM para clasificar intención."""
    prompt = f"""Clasifica la intención del usuario en UNA palabra:

Opciones: agendar, reagendar, cancelar, consultar, otro

Mensaje: "{message}"

Respuesta (solo la palabra):"""

    # Reutilizar LLM de AppointmentAgent o crear uno pequeño
    from langchain_openai import ChatOpenAI
    llm = ChatOpenAI(model="gpt-3.5-turbo", temperature=0)
    response = await llm.ainvoke(prompt)
    intent = response.content.lower().strip()

    valid_intents = ["agendar", "reagendar", "cancelar", "consultar", "otro"]
    return intent if intent in valid_intents else "otro"
```

**Nota**: Si no queremos coste de LLM, mantener keywords por ahora.

---

### Tarea 2.2: Añadir manejo de intención "otro"

**Archivo**: `agents/router_agent.py`

**Problema**: Si `intent="otro"`, `_create_agent_for_intent` devuelve `None` y `process_message` retorna "No puedo manejar esa solicitud". Muy frío.

**Solución**: Mensaje amigable + sugerencias.

```python
def _create_agent_for_intent(self, intent: str) -> Optional[Any]:
    """Factory: crea agente especializado."""
    AGENT_REGISTRY = {
        "agendar": AppointmentAgent,
        "reagendar": RescheduleAgent,
        "cancelar": CancelAgent,
        "consultar": InfoAgent,
    }

    agent_class = AGENT_REGISTRY.get(intent)
    if not agent_class:
        logger.warning("No agent registered for intent", intent=intent)
        return None
    # ... crear instancia

async def process_message(self, message: str) -> str:
    intent = await self._classify_intent(message)
    logger.info("Intent detected", intent=intent, message=message[:50])

    specialized_agent = self._create_agent_for_intent(intent)

    if specialized_agent:
        await specialized_agent.initialize()
        try:
            response = await specialized_agent.process_message(message)
            return response
        except Exception as e:
            logger.error("Error in specialized agent", intent=intent, error=str(e))
            return f"Error procesando tu solicitud: {str(e)}"
    else:
        # Respuesta amigable para intenciones no soportadas
        return """😅 Lo siento, aún no puedo ayudarte con eso.

Lo que SÍ puedo hacer:
• Agendar una cita nueva
• Consultar disponibilidad
• Cancelar o reagendar citas

¿En qué puedo ayudarte?"""
```

---

## 📋 Fase 3: Crear Tests

### Tarea 3.1: Test de RouterAgent

**Archivo**: `tests/test_router_agent.py`

```python
import pytest
from agents.router_agent import RouterAgent

@pytest.fixture
def router():
    return RouterAgent(
        session_id="test123",
        store=MockStore(),
        project_id=None,
        verbose=False
    )

def test_classify_intent_agendar(router):
    assert router._classify_intent("Quiero agendar una cita") == "agendar"
    assert router._classify_intent("Necesito una reserva para mañana") == "agendar"

def test_classify_intent_reagendar(router):
    assert router._classify_intent("Quiero cambiar mi cita") == "reagendar"
    assert router._classify_intent("Necesito reprogramar") == "reagendar"

def test_classify_intent_cancelar(router):
    assert router._classify_intent("Cancelar cita") == "cancelar"
    assert router._classify_intent("Quiero eliminar mi reserva") == "cancelar"

def test_classify_intent_consultar(router):
    assert router._classify_intent("¿Qué citas tengo?") == "consultar"
    assert router._classify_intent("Ver disponibilidad") == "consultar"

def test_classify_intent_otro(router):
    assert router._classify_intent("Hola, cómo estás") == "otro"
    assert router._classify_intent("¿Qué tiempo hace?") == "otro"
```

---

### Tarea 3.2: Test E2E de AppointmentAgent

**Archivo**: `tests/test_appointment_agent.py`

```python
import pytest
from agents.appointment_agent import AppointmentAgent
from services.appointment_service import AppointmentService

@pytest.fixture
def mock_store():
    store = MockStore()
    return store

@pytest.fixture
def agent(mock_store):
    return AppointmentAgent(
        session_id="test_appointment",
        store=mock_store,
        project_id=None
    )

@pytest.mark.asyncio
async def test_full_appointment_flow(agent):
    """
    Test flujo completo:
    Usuario: "Quiero cita para mañana a las 10"
    → Bot: "¿Cuál es tu nombre?"
    Usuario: "Juan Pérez"
    → Bot: consulta disponibilidad → "¿Confirmas...?"
    Usuario: "sí"
    → Bot: ejecuta agendar_cita → "Cita agendada ✅"
    """
    # Paso 1: Usuario pide cita
    response1 = await agent.process_message("Quiero una cita para mañana a las 10")
    assert "nombre" in response1.lower()
    assert agent.state["current_step"] == "require_name"
    assert agent.state["appointment_date"] == "2026-04-07"  # Ajustado si fin de semana
    assert agent.state["appointment_time"] == "10:00"

    # Paso 2: Usuario da nombre
    response2 = await agent.process_message("Juan Pérez")
    assert "confirm" in response2.lower() or "¿Confirmas" in response2
    assert agent.state["patient_name"] == "Juan Pérez"
    assert agent.state["current_step"] == "confirm"
    assert agent.state["available_slots"] is not None

    # Paso 3: Usuario confirma
    response3 = await agent.process_message("sí")
    assert "agendada" in response3.lower() or "✅" in response3
    assert agent.state["current_step"] == "resolution"
    assert agent.state["result"]["success"] is True
    assert "appointment_id" in agent.state["result"]

@pytest.mark.asyncio
async def test_confirmation_does_not_loop(agent):
    """
    Test específico: después de "sí", NO debe volver a pedir confirmación.
    """
    # Setup: estado hasta confirmación
    await agent.process_message("Quiero cita para mañana a las 10")
    await agent.process_message("Juan Pérez")

    # Confirmación
    response = await agent.process_message("sí")
    assert "agendada" in response.lower()

    # Estado debe ser "resolution" o "finished", NO "confirm"
    assert agent.state["current_step"] in ["resolution", "finished"]

    # Si el usuario envía otro mensaje después, debe ser recibido
    # pero no volver a preguntar confirmación
    response2 = await agent.process_message("gracias")
    assert "gracias" in response2.lower() or "✅" in response2
```

---

### Tarea 3.3: Test de integración (webhook → agent)

**Archivo**: `tests/test_integration_specialized_agents.py`

```python
@pytest.mark.asyncio
async def test_webhook_with_router_agent(orchestrator, mock_whatsapp_service):
    """
    Test end-to-end: webhook → Router → AppointmentAgent → response
    """
    payload = {
        "sender": "+1234567890",
        "message": "Quiero una cita para mañana a las 10",
        "message_type": "text"
    }

    response = await orchestrator.process_webhook(payload)

    assert response is not None
    assert "¿Cuál es tu nombre?" in response
```

---

## 📋 Fase 4: Validación Manual Paso a Paso

### Checklist de pruebas manuales

```
☐ 1. Router clasifica correctamente
   Mensaje: "Quiero agendar una cita"
   Esperado: clasifica como "agendar", deriva a AppointmentAgent

☐ 2. Flujo de agendado completo
   a. Usuario: "Quiero cita para mañana a las 10"
      → Bot pide nombre
   b. Usuario: "Carlos Pérez"
      → Bot consulta disponibilidad
      → Bot pregunta "¿Confirmas...?"
   c. Usuario: "sí"
      → Bot ejecuta agendar_cita
      → Bot responde "✅ Cita agendada para Carlos Pérez..."

☐ 3. Sin bucle de confirmación
   Después de paso 2c, si usuario envía "gracias"
   → Bot NO vuelve a preguntar "¿Confirmas...?"
   → Bot responde con cita agendada o mensaje final

☐ 4. Ajuste de fin de semana
   Usuario: "Quiero cita para el domingo a las 10"
   → Bot ajusta al lunes (o siguiente día laborable)

☐ 5. Intención ambigua
   Usuario: "Hola, qué tal"
   → Router clasifica como "otro"
   → Bot responde mensaje amigable con opciones

☐ 6. Fallback a DeyyAgent (si AGENT_ARCHITECTURE=monolithic)
   Cambiar .env: AGENT_ARCHITECTURE=monolithic
   → Mismo flujo pero usando DeyyAgent
```

---

## 📋 Fase 5: Deprecar DeyyAgent (una vez validado)

### Tarea 5.1: Marcar DeyyAgent como legacy

**Archivo**: `agents/deyy_agent.py`

```python
class DeyyAgent:
    """
    ⚠️  LEGACY: Agente monolítico antiguo.
    Se mantiene por compatibilidad backwards.
    Usar RouterAgent + especializados para nuevos despliegues.
    """
    ...
```

**Archivo**: `README.md`

Añadir sección "Migración a Agentes Especializados" con instrucciones.

---

### Tarea 5.2: Eliminar DeyyAgent (solo si todo pasa tests)

**Solo después de 2 semanas sin incidencias con nueva arquitectura**.

1. Backup de `agents/deyy_agent.py` a `agents/deyy_agent.py.bak`
2. Eliminar archivo
3. Eliminar `graphs/deyy_graph.py`
4. Actualizar imports en orchestrator

---

## 📋 Fase 6: Crear Agentes Adicionales

### RescheduleAgent

**Archivo**: `agents/reschedule_agent.py`

**State graph**: `graphs/reschedule_graph.py`

**Flujo**:

```
1. Verificar citas existentes del usuario (obtener_citas_cliente)
2. Elegir cita a reprogramar
3. Pedir nueva fecha/hora
4. Consultar disponibilidad
5. Confirmar cambio
6. Ejecutar reagendar_cita()
```

---

### CancelAgent

**Archivo**: `agents/cancel_agent.py`

**State graph**: `graphs/cancel_graph.py`

**Flujo**:

```
1. Verificar citas existentes
2. Elegir cita a cancelar
3. Pedir confirmación ("¿Seguro que quieres cancelar?")
4. Ejecutar cancelar_cita()
```

---

### InfoAgent

**Archivo**: `agents/info_agent.py`

**State graph**: `graphs/info_graph.py`

**Flujo**:

```
1. Consultar citas del usuario
2. Mostrar próxima cita(s)
3. Opción: ver disponibilidad general
4. Sin tools complejas, solo consultas
```

---

## ⚡ Comandos de Ejecución

### Iniciar servidor

```bash
# Con nueva arquitectura (default)
./run.sh start

# Con arquitectura legacy (rollback)
AGENT_ARCHITECTURE=monolithic ./run.sh start
```

### Ver logs en tiempo real

```bash
./run.sh logs
# Buscar líneas:
# - "RouterAgent"
# - "AppointmentAgent"
# - "Intent detected"
```

### Ejecutar tests

```bash
# Solo tests de router
pytest tests/test_router_agent.py -v

# Solo tests de appointment agent
pytest tests/test_appointment_agent.py -v

# Tests de integración
pytest tests/test_integration_specialized_agents.py -v

# Todos
./run.sh test
```

### Validar configuración

```bash
python -m arcadium_automation validate
```

---

## 🐛 Troubleshooting

### Problema: RouterAgent no clasifica correctamente

**Síntoma**: Mensajes van a "otro" cuando deberían ser "agendar".

**Diagnóstico**:

```bash
# Activar logs detallados
LOG_LEVEL=DEBUG ./run.sh start
```

**Solución**: Mejorar keywords en `router_agent.py` o implementar LLM classification.

---

### Problema: AppointmentAgent no pasa de cierto paso

**Síntoma**: Bot se queda en "¿Cuál es tu nombre?" aunque usuario ya la dio.

**Diagnóstico**:

```bash
# Revisar state en DB
psql -d arcadium -c "SELECT * FROM agent_states WHERE session_id='xxx'"
```

**Causa común**: `intake_node` no está extrayendo bien la información.

**Solución**: Debuggear `intake_node` → `_extract_info_from_message()`.

---

### Problema: Después de "sí", vuelve a consultar disponibilidad

**Síntoma**: Bucle de confirmación persistente.

**Diagnóstico**: Revisar transición en `confirm_node`.

**Causa**: `confirm_node` no detecta "sí" correctamente (patrón regex incompleto).

**Solución**: Añadir más variantes:

```python
affirmations = ["sí", "si", "ok", "confirmo", "confirmado", "sí por favor", "correcto"]
```

---

### Problema: Tools no se ejecutan

**Síntoma**: Agente responde pero no aparece ToolMessage en logs.

**Diagnóstico**: Verificar que `_execute_tool` en `base_specialized_agent.py` esté funcionando.

**Causa**: Tools no registradas en `self._tools`.

**Solución**: En `AppointmentAgent.__init__`, registrar tools:

```python
self._tools = {
    "consultar_disponibilidad": consultar_disponibilidad,
    "agendar_cita": agendar_cita,
}
```

---

## 📊 Métricas de Éxito

### KPIs funcionales

| KPI                            | Target     | Cómo medir                                          |
| ------------------------------ | ---------- | --------------------------------------------------- |
| Precisión clasificación Router | >95%       | Test unitario con 50 ejemplos                       |
| Bucle de confirmación          | 0%         | Test de regresión `test_confirmation_does_not_loop` |
| Latencia total agendado        | <3s        | Benchmark                                           |
| Throughput                     | >1 msg/seg | Benchmark                                           |
| Estado final "resolution"      | 100%       | E2E test                                            |

---

## 📅 Timeline Estimado

| Día     | Tarea                                            | Estado       |
| ------- | ------------------------------------------------ | ------------ |
| Día 1   | Fase 1: Orchestrator usa RouterAgent             | ⏳ Pendiente |
| Día 1-2 | Fase 2: Robustecer Router + manejo "otro"        | ⏳ Pendiente |
| Día 2   | Fase 3: Tests (router, appointment, integración) | ⏳ Pendiente |
| Día 3   | Fase 4: Validación manual + debugging            | ⏳ Pendiente |
| Día 4   | Fase 5: Deprecar DeyyAgent (si todo OK)          | ⏳ Pendiente |
| Día 5-7 | Fase 6: Crear RescheduleAgent, CancelAgent       | ⏳ Pendiente |

**Total**: 1 semana para migración completa.

---

## ✅ Checklist Final

Antes de dar por terminada la migración:

- [ ] RouterAgent clasifica correctamente en >95% de casos
- [ ] AppointmentAgent completa flujo sin bucle
- [ ] Test `test_confirmation_does_not_loop` pasa
- [ ] Logs muestran transiciones claras: `current_step` cambia correctamente
- [ ] Estado se persiste en DB tras cada paso
- [ ] Rollback funciona: cambiar `AGENT_ARCHITECTURE=monolithic` restaura DeyyAgent
- [ ] Todos los tests existentes siguen pasando (no romper funcionalidad)
- [ ] Documentación actualizada (ARQUITECTURA_AGENTES_ESPECIALIZADOS.md)
- [ ] Equipo entrenado en nueva arquitectura

---

## 🔄 Rollback Plan

Si después de implementar hay problemas graves:

```bash
# 1. Cambiar a legacy inmediatamente
vi .env  # AGENT_ARCHITECTURE=monolithic

# 2. Reiniciar
./run.sh restart

# 3. Verificar que funciona
./run.sh logs  # Debería decir "DeyyAgent created"

# 4. Revertir cambios en orchestrator si fue modificado
git checkout core/orchestrator.py
```

**Tiempo de rollback**: < 2 minutos.

---

## 📞 Contacto y Soporte

**Documentación**:

- `ARQUITECTURA_AGENTES_ESPECIALIZADOS.md` - Guía completa
- `CLAUDE.md` - Instrucciones para Claude Code

**Comandos útiles**:

```bash
make test        # Ejecutar tests
make lint        # Verificar código
./run.sh logs    # Ver logs
./run.sh shell   # Shell interactivo
```

---

**🎯 Listos para implementar. Empezar por Fase 1.**
