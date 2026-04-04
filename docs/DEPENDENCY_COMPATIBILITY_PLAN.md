# Plan de Compatibilidad de Dependencias - Arcadium Automation

**Fecha:** 2026-04-04  
**Estado:** Crítico  
**Objetivo:** Establecer un conjunto de dependencias estable y compatible para el proyecto

---

## 📊 Análisis de la Situación Actual

### Versiones Instaladas (venv actual)

```
langchain-core:        1.2.26
langchain:             1.2.15
langchain-openai:      1.1.12
langchain-community:   0.4.1
langchain-postgres:    0.0.17
langgraph:             1.1.6
langgraph-checkpoint:  4.0.1
langgraph-prebuilt:    1.0.9
langgraph-sdk:         0.3.12
openai:                2.30.0
```

### Problemas Identificados

#### 1. ❌ API de LangChain v1.x vs código para v0.2.x

El código en `agents/langchain_compat.py` asume que `create_openai_tools_agent` existe, pero en LangChain v1.x **fue removido**. Sin embargo, nuestra implementación manual en el compat layer **funciona correctamente** ✅.

**Estado:** YA RESUELTO mediante wrapper custom en `langchain_compat.py`.

#### 2. ❌ `langgraph.checkpoint.postgres` no disponible en langgraph 1.x

**Error:** `No module named 'langgraph.checkpoint.postgres'`

En versiones anteriores de langgraph (<1.0), existía `langgraph.checkpoint.postgres.PostgresSaver`. En langgraph 1.x, este módulo fue movido a un paquete separado (`langgraph-checkpoint-postgres`) o eliminado.

**Impacto:** Sin persistencia de checkpoints en PostgreSQL. Usamos MemorySaver como fallback (en memoria, no persistente entre restarts).

**Estado:** ⚠️ **CRÍTICO** - Necesita resolución.

#### 3. ✅ Compatibilidad de APIs resueltas

- `ContextOverflowError`: Disponible en `langchain_core.exceptions` ✅
- `format_tool_to_openai_tool`: Disponible en `langchain_community.tools.convert_to_openai` ✅
- `BaseTool`, `ToolMessage`: Disponibles en `langchain_core.tools` y `langchain_core.messages` ✅

---

## 🎯 Opciones de Resolución

### **OPCIÓN A: Downgrade a LangChain 0.2.x (RECOMENDADA)**

**Ventajas:**

- API estable y documentada
- `create_openai_tools_agent` funciona nativamente
- `langgraph.checkpoint.postgres` disponible
- Menos código de compatibilidad
- Mayor probabilidad de que todas las dependencias co-existan

**Desventajas:**

- No es la última versión
- Requiere congelar versiones específicas

**Versiones objetivo:**

```txt
langchain-core>=0.2.43,<0.3.0
langchain>=0.2.17,<0.3.0
langchain-openai>=0.1.0,<0.2.0
langchain-community>=0.0.10,<0.1.0
langgraph==0.0.35   # Versión que incluye checkpoint.postgres
langgraph-checkpoint==0.0.8  # O la que sea compatible
```

**Paquetes a excluir (problemáticos):**

- ❌ `langgraph-prebuilt>=1.0` (requiere langchain-core>=1.0)
- ❌ `langgraph-sdk` (no necesario para este proyecto)
- ✅ `langchain-postgres` (permanece, es para vector store)

---

### **OPCIÓN B: Migrar a LangChain v1.x (NO RECOMENDADA)**

**Ventajas:**

- Últimas versiones
- Mejoras de rendimiento

**Desventajas:**

- Breaking changes significativos:
  - `create_openai_tools_agent` removido (ya lo solucionamos con wrapper)
  - `AgentExecutor` removido
  - `format_to_openai_tool_messages` removido (ya tenemos fallback)
  - `langgraph.checkpoint.postgres` no existe → necesitaríamos implementar PostgresSaver manualmente
- Mayor complejidad de mantenimiento
- Librerías auxiliares (`langgraph-prebuilt`) incompatibles

**Work requerido:**

1. ✅ `langchain_compat.py` - Ya implementado wrapper
2. ❌ Implementar `PostgresSaver` custom usando SQLAlchemy directo
3. ❌ Revisar todos los usos de APIs removidas
4. ❌ Actualizar tests que dependan de `AgentExecutor`

---

### **OPCIÓN C: Híbrida (LangChain core moderno + componentes legacy)**

Usar:

- `langchain-core` v1.x (nuevo)
- `langchain-openai` v1.x (nuevo)
- `langgraph` v0.x (con checkpoint.postgres)

**Problema:** Incompatible, langchain v1.x requiere langgraph>=1.0.

---

## 📋 Plan de Acción Recomendado: **OPCIÓN A**

### Paso 1: Actualizar `requirements.txt`

```txt
# Core asincrónico y procesamiento de cadenas
aiohttp>=3.9.0

# Validación y configuración
pydantic>=2.0.0
pydantic-settings>=2.0.0
jsonschema>=4.19.0

# Utilidades
python-dotenv>=1.0.0
tenacity>=8.2.0
structlog>=24.1.0
psutil>=5.9.0
watchdog>=3.0.0
requests>=2.31.0
websockets>=12.0

# Persistencia (opcional)
redis>=5.0.0
sqlalchemy>=2.0.0
alembic>=1.13.0
psycopg2-binary>=2.9.9

# Monitoreo
prometheus-client>=0.19.0
opentelemetry-api>=1.20.0
opentelemetry-sdk>=1.20.0

# LangChain & AI (v0.2.x ecosystem)
langchain>=0.2.17,<0.3.0
langchain-core>=0.2.43,<0.3.0
langchain-openai>=0.1.0,<0.2.0
langchain-community>=0.0.10,<0.1.0

# IMPORTANTE: Usar langgraph 0.x para checkpoint.postgres
langgraph==0.0.35
langgraph-checkpoint==0.0.8  # Ajustar si es necesario

# NO usar langgraph-prebuilt>=1.0 (incompatible)
# Si se necesita, usar versión 0.x:
langgraph-prebuilt==0.0.3  # Opcional, puedes eliminarlo si no se usa

# PostgreSQL para vectores (verificar compatibilidad)
langchain-postgres>=0.0.9,<0.1.0
pgvector>=0.2.0

# Web Framework
fastapi>=0.104.0
uvicorn[standard]>=0.24.0
python-multipart>=0.0.6

# HTTP cliente
httpx>=0.25.0

# Google APIs
google-api-python-client>=2.0.0
google-auth-httplib2>=0.1.0
google-auth-oauthlib>=0.4.1
google-auth>=2.0.0

# MCP (Model Context Protocol) - verificar compatibilidad
# Si no es crítico, comentar temporalmente
mcp>=0.9.0
```

**Cambios clave:**

1. Eliminar duplicados (había 2 bloques de LangChain)
2. Especificar rangos de versión estrictos para langchain-core (<0.3.0)
3. Usar `langgraph==0.0.35` en lugar de 1.x
4. Eliminar o downgradear `langgraph-prebuilt` a 0.x
5. Eliminar `langgraph-sdk` (no necesario)
6. Mantener `langchain-postgres` (pero verificar compatibilidad con langchain-core 0.2.x)

---

### Paso 2: Recrear entorno virtual

```bash
# Backup del venv actual (opcional)
mv venv venv.backup

# Crear nuevo venv
python3 -m venv venv
source venv/bin/activate
pip install --upgrade pip

# Instalar desde requirements.txt actualizado
pip install -r requirements.txt

# Verificar compatibilidad
python -c "
from langchain_core.exceptions import ContextOverflowError
from langchain_community.tools.convert_to_openai import format_tool_to_openai_tool
from langchain.agents import create_openai_tools_agent  # Debería existir en v0.2.x
from langgraph.checkpoint.postgres import PostgresSaver  # Debería existir en langgraph 0.x
print('✅ Todas las importaciones críticas OK')
"
```

---

### Paso 3: Simplificar `langchain_compat.py`

Con LangChain 0.2.x, muchos de nuestros workarounds ya no son necesarios. Podemos:

1. **Eliminar** la implementación manual de `format_tool_to_openai_tool` (usar la oficial)
2. **Eliminar** la implementación manual de `format_to_openai_tool_messages` (usar la oficial)
3. **Eliminar** la implementación manual de `OpenAIToolsAgentOutputParser` (usar la oficial)
4. **Simplificar** `create_openai_tools_agent` → usar la función nativa directamente

**Nuevo `langchain_compat.py` (minimalista):**

```python
"""
Compatibilidad para LangChain 0.2.x
Centraliza imports que cambiaron entre versiones.
"""

from langchain_core.language_models import BaseLanguageModel
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.runnables import Runnable
from langchain_core.tools import BaseTool
from langchain.agents import create_openai_tools_agent, AgentExecutor
from langchain.agents.format_scratchpad.openai_tools import format_to_openai_tool_messages
from langchain.agents.output_parsers.openai_tools import OpenAIToolsAgentOutputParser

# Exportar todo lo necesario
__all__ = [
    'create_openai_tools_agent',
    'AgentExecutor',
    'format_to_openai_tool_messages',
    'OpenAIToolsAgentOutputParser',
    'BaseLanguageModel',
    'ChatPromptTemplate',
    'Runnable',
    'BaseTool',
]
```

Esto reduce complejidad y confía en la API oficial.

---

### Paso 4: Verificar `PostgresSaver`

```python
from langgraph.checkpoint.postgres import PostgresSaver

# Si esta importación falla, significa que langgraph 0.x no está instalado correctamente
```

**Uso esperado en `arcadium_graph.py`:**

```python
from langgraph.checkpoint.postgres import PostgresSaver

# En state machine agent init:
if USE_POSTGRES_CHECKPOINT:
    self._checkpointer = PostgresSaver(connection_string=DATABASE_URL)
else:
    from langgraph.checkpoint.memory import MemorySaver
    self._checkpointer = MemorySaver()
```

---

### Paso 5: Ejecutar tests de compatibilidad

Crear `tests/test_dependency_compatibility.py`:

```python
def test_langchain_imports():
    """Verificar que todas las importaciones críticas funcionan"""
    from agents.langchain_compat import (
        create_openai_tools_agent,
        format_to_openai_tool_messages,
        OpenAIToolsAgentOutputParser,
    )
    # Si no hay excepciones, OK

def test_langgraph_imports():
    """Verificar que PostgresSaver está disponible"""
    from langgraph.checkpoint.postgres import PostgresSaver
    assert PostgresSaver is not None

def test_agent_executor_import():
    """Verificar que AgentExecutor existe (legacy compatibility)"""
    from agents.langchain_compat import AgentExecutor
    assert AgentExecutor is not None
```

---

### Paso 6: Actualizar código que usa APIs eliminadas

Revisar:

- ¿`AgentExecutor` se usa en algún lugar? Si no, eliminar referencias.
- ¿`langgraph.prebuilt` se usa? Si no, eliminar `langgraph-prebuilt` de requirements.
- ¿`create_openai_tools_agent` se usa desde donde? Asegurar que todas las llamadas usen `agents.langchain_compat.create_openai_tools_agent`.

---

## 📈 Matriz de Compatibilidad Versiones Objetivo

| Paquete              | Versión Objetivo | Rango Aceptable  | Notas                                      |
| -------------------- | ---------------- | ---------------- | ------------------------------------------ |
| langchain-core       | 0.2.43           | >=0.2.43, <0.3.0 | Core de LangChain                          |
| langchain            | 0.2.17           | >=0.2.17, <0.3.0 | Alta-level abstractions                    |
| langchain-openai     | 0.1.25           | >=0.1.0, <0.2.0  | Integración OpenAI                         |
| langchain-community  | 0.0.10           | >=0.0.10, <0.1.0 | Herramientas community (convert_to_openai) |
| langgraph            | 0.0.35           | ==0.0.35         | Incluye checkpoint.postgres                |
| langgraph-checkpoint | 0.0.8            | >=0.0.8, <0.1.0  | Checkpointers para langgraph 0.x           |
| langgraph-prebuilt   | (eliminar)       | N/A              | Para v1.x, incompatible                    |
| langchain-postgres   | 0.0.9            | >=0.0.9, <0.1.0  | Verificar compatibilidad                   |

---

## 🚀 Implementación Inmediata

### 1. Modificar `requirements.txt`

Reemplazar el bloque de LangChain con las versiones especificadas arriba.

### 2. Eliminar paquetes innecesarios

- `langgraph-sdk` (no se usa en el código)
- `langgraph-prebuilt>=1.0` (incompatible, eliminar o usar 0.0.3 si es necesario)
- Duplicados en requirements.txt

### 3. Recrear venv

```bash
rm -rf venv
python3 -m venv venv
source venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
```

### 4. Verificar

```bash
# Test de imports críticos
python -c "
from agents.langchain_compat import create_openai_tools_agent, format_to_openai_tool_messages, OpenAIToolsAgentOutputParser
from langgraph.checkpoint.postgres import PostgresSaver
from langchain_core.exceptions import ContextOverflowError
print('✅ Todo OK')
"
```

### 5. Ejecutar tests

```bash
./run.sh test
# O
pytest tests/test_state_machine_integration.py -v
```

---

## ⚠️ Riesgos y Consideraciones

1. **LangChain 0.2.x es EOL?** Puede que no reciba actualizaciones de seguridad.
   - **Mitigación:** Revisar periódicamente si hay parches. Si el proyecto tiene vida larga, considerar migrar a v1.x en el futuro con una implementación propia de `create_openai_tools_agent`.

2. **`langgraph-prebuilt`**: Si el proyecto requiere funcionalidades de `langgraph.prebuilt`, necesitaremos:
   - O migrar a langgraph 1.x y reimplementar `PostgresSaver`
   - O buscar alternativas en langgraph 0.x

3. **Funcionalidades nuevas:** LangChain v1.x tiene mejoras. Al downgradear, las perdemos.
   - **Aceptable** para un proyecto estable que prioriza confiabilidad sobre novedades.

4. **Dependencias transitivas:** Al bajar versiones, otras librerías pueden romper.
   - **Pruebas exhaustivas** después del downgrade.

---

## 🎯 Decisión Final

**Recomendación: OPCIÓN A (Downgrade a 0.2.x)**

**Razones:**

1. ✅ Es la ruta más simple y probada
2. ✅ `langgraph.checkpoint.postgres` vuelve a estar disponible
3. ✅ Menos código de compatibilidad custom
4. ✅ Menos bug surface (probada en production por otros proyectos)
5. ✅ Los tests de Fase 4 ya demostraron que el agente funciona conceptualmente

**Timeline:**

- Día 1: Actualizar requirements.txt + recrear venv
- Día 2: Ejecutar tests, corregir breaks menores
- Día 3: Validar funcionalidad completa + documentar

---

## 📝 Checklist Pre-Implementación

- [ ] Backup de `requirements.txt` actual
- [ ] Backup de `venv/` (opcional)
- [ ] Revisar que ningún paquete requiera específicamente langchain>=1.0
- [ ] Verificar que `mcp>=0.9.0` no tenga conflicto con langchain 0.2.x
- [ ] Asegurar que `langchain-postgres` 0.0.9 sea compatible con langchain-core 0.2.43
- [ ] Documentar las versiones exactas en `docs/DEPENDENCIES.md`
- [ ] Crear issue de seguimiento: "Migrate to LangChain v1.x (future work)"

---

**Responsable:** Fase 4 - Compatibilidad  
**Fecha límite:** Inmediata (bloquea pruebas end-to-end)  
**Depende de:** Aprobación del plan
