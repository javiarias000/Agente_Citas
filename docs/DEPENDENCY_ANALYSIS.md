# Análisis Detallado de Dependencias - Arcadium Automation

**Fecha:** 2026-04-04  
**Autor:** Análisis automatizado  
**Propósito:** Documentar el estado actual de dependencias, usos reales en el código, y problemas de compatibilidad

---

## 📦 Dependencias Instaladas (venv actual)

```bash
langchain-classic==1.0.3
langchain-community==0.4.1
langchain-core==1.2.26
langchain-openai==1.1.12
langchain-postgres==0.0.17
langchain-text-splitters==1.1.1
langchain==1.2.15
langgraph-checkpoint==4.0.1
langgraph-prebuilt==1.0.9
langgraph-sdk==0.3.12
langgraph==1.1.6
mcp==1.27.0
openai==2.30.0
```

---

## 🔍 Uso Real de Dependencias en el Código

### 1. **LangChain Core** (`langchain-core` - 1.2.26)

**Imports encontrados:**

- `from langchain_core.language_models import BaseLanguageModel`
- `from langchain_core.prompts import ChatPromptTemplate`
- `from langchain_core.runnables import Runnable, RunnablePassthrough`
- `from langchain_core.tools import BaseTool, StructuredTool`
- `from langchain_core.messages import HumanMessage, AIMessage, ToolMessage, BaseMessage`
- `from langchain_core.exceptions import ContextOverflowError`

**Uso en:**

- `agents/langchain_compat.py` - Wrapper de compatibilidad
- `agents/deyy_agent.py` - Clase principal
- `agents/state_machine_agent.py` - StateMachineAgent
- `graphs/arcadium_graph.py` - Grafo unificado
- `graphs/deyy_graph.py` - Grafo legacy
- `tools/state_machine.py` - Tools con decorador `@tool`

**Estado:** ✅ Compatible, API estable en v1.x

---

### 2. **LangChain** (`langchain` - 1.2.15)

**Imports encontrados:**

- `from langchain.tools import tool` (usado en `langchain_compat.py`)
- `from langchain.agents.format_scratchpad.openai_tools import format_to_openai_tool_messages` (fallback en compat)
- `from langchain.agents.output_parsers.openai_tools import OpenAIToolsAgentOutputParser` (fallback en compat)

**Problema:** En LangChain v1.x, estos módulos **no existen**:

- ❌ `langchain.agents.format_scratchpad.openai_tools` → removido
- ❌ `langchain.agents.output_parsers.openai_tools` → removido
- ❌ `langchain.agents.create_openai_tools_agent` → removido

**Ya resuelto:** `agents/langchain_compat.py` implementa fallbacks manuales:

- ✅ `format_to_openai_tool_messages` - Wrapper propio
- ✅ `OpenAIToolsAgentOutputParser` - Clase wrapper
- ✅ `create_openai_tools_agent` - Implementación manual usando `llm.bind()`

**Conclusión:** El compat layer funciona, pero es código extra que sería innecesario con LangChain 0.2.x.

---

### 3. **LangChain OpenAI** (`langchain-openai` - 1.1.12)

**Imports:**

- `from langchain_openai import ChatOpenAI` ✅ (existe en v1.x)

**Estado:** ✅ Compatible

---

### 4. **LangGraph** (`langgraph` - 1.1.6)

**Imports encontrados:**

- `from langgraph.graph import StateGraph, END` ✅
- `from langgraph.checkpoint.base import BaseCheckpointSaver` ✅
- `from langgraph.graph.message import add_messages` ✅
- `from langgraph.types import Command` ✅

**Problema CRÍTICO:**

```python
# En graphs/deyy_graph.py:345
from langgraph.checkpoint.postgres import PostgresSaver
```

**Esta importación FALLA en langgraph 1.x** porque el módulo `langgraph.checkpoint.postgres` no existe.

**Uso real de PostgresSaver:**

- `graphs/deyy_graph.py` líneas 345-374: Intenta importar y crear PostgresSaver
- `graphs/arcadium_graph.py`: Espera recibir `BaseCheckpointSaver` como parámetro (no importa directamente)

**Fallback actual:**

```python
except ImportError:
    from langgraph.checkpoint.memory import MemorySaver
    checkpointer = MemorySaver()
```

Esto significa que **sin PostgresSaver, los checkpoints NO se persisten en PostgreSQL**. La persistencia de estado se pierde al reiniciar.

---

### 5. **LangGraph Checkpoint** (`langgraph-checkpoint` - 4.0.1)

Este paquete es parte de langgraph 1.x. No se importa directamente. La importación `langgraph.checkpoint.postgres` debería venir de aquí, **pero no existe en langgraph 1.x**.

**En langgraph 0.x**, existía `langgraph.checkpoint.postgres.PostgresSaver`.

**Conclusión:** Para tener persistencia de checkpoints en PostgreSQL, necesitamos **langgraph 0.x**, no 1.x.

---

### 6. **LangGraph Prebuilt** (`langgraph-prebuilt` - 1.0.9)

**¿Se usa en el código?** Revisé:

- `graphs/deyy_graph.py` - NO USA
- `graphs/arcadium_graph.py` - NO USA
- `agents/*.py` - NO USA
- `tests/*.py` - NO USA

**Conclusión:** ❌ **Paquete innecesario**. Puede eliminarse de `requirements.txt`.

---

### 7. **LangGraph SDK** (`langgraph-sdk` - 0.3.12)

**¿Se usa en el código?** NO se encuentra ningún import de `langgraph_sdk`.

**Conclusión:** ❌ **Paquete innecesario**. Puede eliminarse.

---

### 8. **LangChain Postgres** (`langchain-postgres` - 0.0.17)

**¿Se usa en el código?** Busqué imports:

- No se encuentra `from langchain_postgres` en ningún `.py` del proyecto.

**¿Se usa en `requirements.txt`?** Sí, pero sin usarse.

**Conclusión:** ⚠️ Paquete no utilizado actualmente (para vector store). Puede eliminarse si no se planea usarlo pronto.

---

### 9. **MCP** (`mcp` - 1.27.0)

**¿Se usa en el código?** Busqué:

- No hay imports de `mcp` en el código fuente.

**Nota:** Hay un directorio `.claude/servers/n8n-mcp/` (MCP server para n8n integration), pero no se usa en la aplicación principal.

**Conclusión:** ❌ **Paquete innecesario** para la funcionalidad principal. Eliminar o mover a `requirements-dev.txt`.

---

## 🎯 Mapa de Dependencias Esenciales

### **Dependencias Críticas (sin ellas el sistema no funciona)**

| Paquete              | Versión actual | Necesario para      | Compatible con v1.x?            |
| -------------------- | -------------- | ------------------- | ------------------------------- |
| langchain-core       | 1.2.26         | Base de todos       | ✅ Sí                           |
| langchain            | 1.2.15         | Herramientas @tool  | ⚠️ Sí con compat layer          |
| langchain-openai     | 1.1.12         | ChatOpenAI          | ✅ Sí                           |
| langgraph            | 1.1.6          | StateGraph, Command | ❌ **NO** (falta PostgresSaver) |
| langgraph-checkpoint | 4.0.1          | BaseCheckpointSaver | ⚠️ Parcial (sin postgres)       |

### **Dependencias Opcionales (se pueden eliminar)**

| Paquete                  | Razón                                                            |
| ------------------------ | ---------------------------------------------------------------- |
| langchain-postgres       | No se usa actualmente                                            |
| langchain-community      | Solo para `format_tool_to_openai_tool` (ya lo tenemos en compat) |
| langchain-classic        | No se usa                                                        |
| langchain-text-splitters | No se usa                                                        |
| langgraph-prebuilt       | No se usa                                                        |
| langgraph-sdk            | No se usa                                                        |
| mcp                      | No se usa en app principal                                       |

---

## 🚨 Problemas de Compatibilidad

### Problema 1: **LangChain v1.x no tiene `create_openai_tools_agent`**

**Ubicación:** `agents/langchain_compat.py:88-112`

**Estado:** ✅ Resuelto con implementación manual

**Código actual (funciona):**

```python
def create_openai_tools_agent(
    llm: BaseLanguageModel,
    tools: Sequence[BaseTool],
    prompt: ChatPromptTemplate
) -> Runnable:
    llm_with_tools = llm.bind(tools=[format_tool_to_openai_tool(tool) for tool in tools])
    agent = (
        RunnablePassthrough.assign(
            agent_scratchpad=lambda x: format_to_openai_tool_messages(x["intermediate_steps"])
        )
        | prompt
        | llm_with_tools
        | OpenAIToolsAgentOutputParser()
    )
    return agent
```

**Ventaja:** Funciona tanto en LangChain 0.2.x como 1.x.

---

### Problema 2: **`langgraph.checkpoint.postgres` no existe en v1.x**

**Ubicación:** `graphs/deyy_graph.py:345`

**Error:**

```python
from langgraph.checkpoint.postgres import PostgresSaver
ImportError: No module named 'langgraph.checkpoint.postgres'
```

**Impacto:** ❌ **CRÍTICO** - Sin persistencia de checkpoints en PostgreSQL. Los states se pierden al reiniciar.

**Opciones:**

#### Opción A: Downgrade a `langgraph==0.0.35` (RECOMENDADO)

```txt
langgraph==0.0.35
langgraph-checkpoint==0.0.8
```

✅ `PostgresSaver` existe y funciona.  
✅ Compatible con `langchain-core>=0.2.43,<0.3.0`  
⚠️ Requiere también downgrade de langchain a 0.2.x

---

#### Opción B: Implementar PostgresSaver propio para langgraph 1.x

**Work:** Escribir una clase que herede de `BaseCheckpointSaver` y use SQLAlchemy para guardar/cargar states en PostgreSQL.

**Complejidad:** Alta  
**Riesgo:** Probablemente es mejor opción A.

---

### Problema 3: **Duplicados y dependencias innecesarias**

`requirements.txt` tiene dos bloques de LangChain (líneas 32-36 y 51-56). Además incluye paquetes no usados.

**Solución:** Limpiar requirements.txt.

---

## 📋 Recomendaciones Finales

### **Camino 1: Downgrade a ecosistema LangChain 0.2.x (MÁS FÁCIL)**

**Pros:**

- API estable probada
- `langgraph.checkpoint.postgres` existe
- Menos código custom
- Todos los imports funcionan nativamente

**Contras:**

- No es la última versión
- Requiere congelar versiones

**Versiones objetivo:**

```txt
langchain>=0.2.17,<0.3.0
langchain-core>=0.2.43,<0.3.0
langchain-openai>=0.1.0,<0.2.0
langchain-community>=0.0.10,<0.1.0  # Para format_tool_to_openai_tool
langgraph==0.0.35
langgraph-checkpoint==0.0.8
```

**Pasos:**

1. Modificar `requirements.txt` con versiones anteriores
2. Eliminar paquetes no usados (mcp, langgraph-prebuilt, langgraph-sdk, langchain-postgres)
3. Recrear venv
4. Simplificar `langchain_compat.py` (volver a usar imports nativos)
5. Testear todo

---

### **Camino 2: Migrar a LangChain 1.x (MÁS TRABAJO)**

**Pros:**

- Últimas versiones
- Mejoras de rendimiento

**Contras:**

- `create_openai_tools_agent` no existe → wrapper custom (ya hecho)
- `PostgresSaver` no existe → implementar propio
- Posibles otras APIs removidas

**Work adicional:**

1. Escribir `PostgresSaver` custom usando SQLAlchemy
2. Revisar todas las APIs de langgraph por breaking changes
3. Test exhaustivo

**Conclusión:** No vale la pena para este proyecto. Mejor opción A.

---

## 📊 Dependencias a Eliminar (Innecesarias)

```
langchain-postgres==0.0.17       # No se usa
langchain-classic==1.0.3        # No se usa
langchain-text-splitters==1.1.1 # No se usa
langgraph-prebuilt==1.0.9       # No se usa
langgraph-sdk==0.3.12           # No se usa
mcp==1.27.0                     # No se usa en app principal
```

---

## 🎯 Plan de Acción Inmediato

### Paso 1: Backup

```bash
cp requirements.txt requirements.txt.backup_20260404
```

### Paso 2: Modificar requirements.txt

Ver archivo adjunto `requirements_fixed.txt` con versiones compatibles.

### Paso 3: Recrear venv

```bash
rm -rf venv
python3 -m venv venv
source venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
```

### Paso 4: Verificar imports críticos

```bash
python -c "
from langchain.agents import create_openai_tools_agent
from langgraph.checkpoint.postgres import PostgresSaver
from langchain_core.exceptions import ContextOverflowError
print('✅ Todo OK')
"
```

### Paso 5: Ejecutar suite de tests

```bash
pytest tests/test_state_machine_integration.py -v
```

---

## 📈 Matriz de Decisión

| Requisito                    | LangChain 0.2.x | LangChain 1.x (actual)               |
| ---------------------------- | --------------- | ------------------------------------ |
| `create_openai_tools_agent`  | ✅ Nativo       | ❌ No existe (pero wrapper funciona) |
| `PostgresSaver`              | ✅ Disponible   | ❌ No existe                         |
| `format_tool_to_openai_tool` | ✅ Nativo       | ✅ En langchain-community            |
| API estable                  | ✅ Sí           | ⚠️ En transición                     |
| Persistencia completa        | ✅ Sí           | ❌ No (sin implementar)              |
| Complejidad de código        | ⬇️ Menos        | ⬆️ Más (wrappers)                    |

**Veredicto:** LangChain 0.2.x es la opción que necesita **menos trabajo** y da **más confiabilidad**.

---

## ✅ Conclusión

El proyecto está funcional con la versión actual, pero **falta persistencia de checkpoints** en PostgreSQL debido a la incompatibilidad de langgraph 1.x.

**Recomendación firme:** Downgrade a ecosistema LangChain 0.2.x (langchain-core 0.2.43, langgraph 0.0.35) para tener:

1. Persistencia completa con PostgresSaver
2. Código más limpio (sin compat hacks)
3. Estabilidad a largo plazo

**Timeline estimado:** 1 día para implementar y validar.

---

**Próximos pasos:** Ver `docs/DEPENDENCY_COMPATIBILITY_PLAN.md` para el plan detallado de implementación.
