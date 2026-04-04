# 🎯 PLAN DE IMPLEMENTACIÓN DETALLADO: LANGCHAIN/GRAPHCHAIN

## Arcadium Automation - Integración completa del workflow JSON

---

## 📊 ESTADO ACTUAL DEL PROYECTO

### ✅ COMPONENTES IMPLEMENTADOS (Funcionando)

- [x] **Core Landchain System** - Sistema de cadenas con garantía 100%
- [x] **Orchestrator** - Orquestación central
- [x] **State Manager** - Gestión de estado (memory/redis/sqlite)
- [x] **n8n Client** - Cliente HTTP para n8n API
- [x] **Validators** - Esquemas Pydantic
- [x] **Unified Chain** - Cadena base de procesamiento
- [x] **Audio Transcription** - Integración Whisper
- [x] **Monitoring** - Métricas Prometheus
- [x] **Logging** - Sistema de logs estructurados

### ❌ COMPONENTES FALTANTES (LangChain/GraphChain)

1. **Agente_Deyy** - LangChain Agent con herramientas
2. **LLM_Deyy** - LM Chat OpenAI (GPT-4/3.5)
3. **LLM_Divisor** - LM Chat OpenAI para división
4. **Postgres_Memory_Deyy** - Memoria de conversación en PostgreSQL
5. **Planificador_Obligatorio** - Herramienta Code personalizada
6. **Think** - Herramienta Think (razonamiento)
7. **MCP_GoogleCalendar** - Cliente MCP para Google Calendar
8. **Supabase_KnowledgeBase** - Vector Store para knowledge base
9. **Embeddings_OpenAI** - Embeddings para vectores
10. **Divisor_Mensajes** - Chain LLM para división de mensajes

---

## 🎯 FASE 1: PREPARACIÓN Y DEPENDENCIAS

### 1.1 Instalar Dependencias LangChain

```bash
pip install \
  langchain>=0.1.0 \
  langchain-openai>=0.0.5 \
  langchain-community>=0.0.10 \
  supabase>=2.3.0 \
  psycopg2-binary>=2.9.9 \
  mcp>=0.9.0
```

### 1.2 Configurar Variables de Entorno Adicionales

```bash
# En .env
OPENAI_API_KEY=sk-...
SUPABASE_URL=https://tu-proyecto.supabase.co
SUPABASE_SERVICE_ROLE_KEY=eyJ...
GOOGLE_CALENDAR_MCP_ENDPOINT=http://localhost:8080
POSTGRES_MEMORY_TABLE=langchain_memory
```

---

## 🏗️ FASE 2: IMPLEMENTACIÓN DE COMPONENTES LANGCHAIN

### 2.1 Módulo `utils/langchain_components.py`

**Responsable:** Agente Especializado LangChain Base

Implementar:

- `OpenAIChatModel` - Wrapper para LLM_Deyy y LLM_Divisor
- `PostgresChatMemory` - Memoria de conversación PostgreSQL
- `EmbeddingsFactory` - Factory para embeddings
- `SupabaseVectorStore` - Vector store knowledge base

**Estructura:**

```python
class LangChainComponentFactory:
    @staticmethod
    def create_chat_model(model="gpt-4", temperature=0.7):
        pass

    @staticmethod
    def create_postgres_memory(session_id, table_name):
        pass

    @staticmethod
    def create_embeddings():
        pass

    @staticmethod
    def create_supabase_vectorstore(table_name, embedder):
        pass
```

### 2.2 Módulo `utils/tools.py`

**Responsable:** Agente Especializado Tools

Implementar herramientas LangChain:

1. **Planificador_Obligatorio** (Code Tool)

```python
class PlanningTool(BaseTool):
    name = "planificador_obligatorio"
    description = "Planifica tareas complejas en pasos ejecutables"

    def _run(self, task: str) -> Dict:
        # Código JavaScript ejecutado en sandbox
        # Implementar lógica de planificación
        pass
```

2. **Think Tool**

```python
class ThinkTool(BaseTool):
    name = "think"
    description = "Razona sobre un problema antes de actuar"

    def _run(self, thought: str) -> str:
        # Proceso de pensamiento estructurado
        return f" razonamiento: {thought}"
```

3. **MCP Google Calendar Tool**

```python
class MCPGoogleCalendarTool(BaseTool):
    name = "mcp_google_calendar"
    description = "Interactúa con Google Calendar via MCP"

    def _run(self, action: str, **kwargs):
        # Llamada a MCP server
        pass

    async def _arun(self, action: str, **kwargs):
        # Async version
        pass
```

### 2.3 Módulo `agents/arcadium_agent.py`

**Responsable:** Agente Especializado Agent Builder

Implementar **Agente_Deyy**:

```python
from langchain.agents import AgentExecutor, create_openai_tools_agent
from langchain_openai import ChatOpenAI
from langchain_core.prompts import ChatPromptTemplate

class DeyyAgent:
    def __init__(self, llm, tools, memory):
        self.prompt = ChatPromptTemplate.from_messages([
            ("system", "Eres Deyy, asistente especializado en Arcadium..."),
            ("human", "{input}"),
            ("placeholder", "{agent_scratchpad}")
        ])
        self.agent = create_openai_tools_agent(llm, tools, self.prompt)
        self.agent_executor = AgentExecutor(
            agent=self.agent,
            tools=tools,
            memory=memory,
            verbose=True,
            handle_parsing_errors=True
        )

    async def run(self, input_text: str):
        return await self.agent_executor.ainvoke({"input": input_text})
```

### 2.4 Módulo `chains/divisor_chain.py`

**Responsable:** Agente Especializado Chain Builder

Implementar **Divisor_Mensajes** (Chain LLM):

```python
from langchain_core.prompts import PromptTemplate
from langchain_core.output_parsers import JsonOutputParser
from pydantic import BaseModel, Field

class MessagePart(BaseModel):
    parte: str = Field(description="Parte del mensaje procesado")
    categoria: str = Field(description="Categoría: comando/pregunta/respuesta")
    prioridad: int = Field(description="Prioridad 1-5")

class DivisorChain:
    def __init__(self, llm):
        self.llm = llm
        self.prompt = PromptTemplate.from_template(
            """Divide el siguiente mensaje en partes lógicas:

Mensaje: {mensaje}

Analiza y divide en partes homogéneas. Considera:
- Cambios de tema
- Múltiples preguntas
- Comandos diferentes

{format_instructions}"""
        )
        self.parser = JsonOutputParser(pydantic_object=MessagePart)

    async def process(self, mensaje: str) -> List[MessagePart]:
        chain = self.prompt | self.llm | self.parser
        result = await chain.ainvoke({
            "mensaje": mensaje,
            "format_instructions": self.parser.get_format_instructions()
        })
        return result if isinstance(result, list) else [result]
```

---

## 🔄 FASE 3: INTEGRACIÓN CON ARCADEUM_CHAINS.PY

### 3.1 Modificar `ArcadiumChainBuilder` para incluir LangChain

**Responsable:** Agente Especializado Integración

Agregar métodos:

```python
class ArcadiumChainBuilder:
    def __init__(self, ...):
        # ... existente
        self._init_langchain_components()

    def _init_langchain_components(self):
        """Inicializa componentes LangChain"""
        from utils.langchain_components import LangChainComponentFactory

        # LLMs
        self.llm_deyy = LangChainComponentFactory.create_chat_model(
            model="gpt-4", temperature=0.7
        )
        self.llm_divisor = LangChainComponentFactory.create_chat_model(
            model="gpt-3.5-turbo", temperature=0.3
        )

        # Memory
        self.memory_deyy = LangChainComponentFactory.create_postgres_memory(
            session_id_template="deyy_{phone}",
            table_name="langchain_memory"
        )

        # Embeddings y Vector Store
        self.embeddings = LangChainComponentFactory.create_embeddings()
        self.vectorstore = LangChainComponentFactory.create_supabase_vectorstore(
            table_name="knowledge_base"
        )

        # Tools
        from utils.tools import get_deyy_tools
        self.tools = get_deyy_tools(
            vectorstore=self.vectorstore,
            llm=self.llm_deyy
        )

        # Agente
        from agents.arcadium_agent import DeyyAgent
        self.agent_deyy = DeyyAgent(
            llm=self.llm_deyy,
            tools=self.tools,
            memory=self.memory_deyy
        )

        # Chain Divisor
        from chains.divisor_chain import DivisorChain
        self.divisor_chain = DivisorChain(llm=self.llm_divisor)

    def build_unified_chain(self, strict: bool = True) -> LandChain:
        # ... existente ...

        # Agregar nuevo eslabón Agente_Deyy antes de INSERT_Cliente
        chain.add_link(
            name="execute_agent_deyy",
            func=self._execute_agent_deyy,
            timeout=300.0,
            continue_on_failure=True,
            metadata={"step": 6.5, "description": "Ejecutar Agente Deyy"}
        )

        # ... resto ...
```

---

## 📋 FASE 4: IMPLEMENTACIÓN ESPECÍFICA POR NODO

### 4.1 Nodo `Agente_Deyy` (LangChain Agent)

**Implementar en:** `agents/arcadium_agent.py`

**Parámetros del workflow JSON:**

- `promptType`: "chat"
- `text`: Prompt del sistema
- `options`: Configuración del agente

**Código Python:**

```python
class DeyyAgent:
    def __init__(self, system_prompt: str, model: str = "gpt-4"):
        self.system_prompt = system_prompt
        self.model = model
        # ... inicialización

    async def execute(self, conversation_context: Dict) -> Dict:
        """
        Ejecuta el agente con el contexto de conversación.

        Args:
            conversation_context: {
                'mensaje': str,
                'conversation_history': List,
                'previous_context': Dict
            }

        Returns:
            {
                'agent_response': str,
                'tool_calls': List,
                'reasoning': str
            }
        """
        pass
```

### 4.2 Nodo `Divisor_Mensajes` (Chain LLM)

**Implementar en:** `chains/divisor_chain.py`

**Función:** Dividir mensajes largos en partes lógicas

**Parámetros JSON:**

- `promptType`: "chain"
- `text`: Prompt de división
- `messages`: Configuración de messages array
- `batching`: Parámetros de batch

### 4.3 Nodo `Postgres_Memory_Deyy` (Memory)

**Implementar en:** `utils/langchain_components.py`

**Tabla PostgreSQL:**

```sql
CREATE TABLE langchain_memory (
    id SERIAL PRIMARY KEY,
    session_id VARCHAR(255) NOT NULL,
    message_type VARCHAR(50), -- 'human'/'ai'/'system'
    content TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT NOW(),
    INDEX idx_session (session_id)
);
```

### 4.4 Nodo `Supabase_KnowledgeBase` (VectorStore)

**Implementar en:** `utils/langchain_components.py`

**Tabla Supabase:**

```sql
CREATE TABLE documents (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    content TEXT NOT NULL,
    metadata JSONB DEFAULT '{}',
    embedding VECTOR(1536)  -- OpenAI embeddings dimension
);
```

**Función:** Búsqueda semántica de conocimiento

### 4.5 Herramientas (Tools)

**Implementar en:** `utils/tools.py`

- **Planificador_Obligatorio**: Ejecuta código JS para planificación
- **Think**: Razonamiento profundo
- **MCP_GoogleCalendar**: Integración MCP

---

## 🧪 FASE 5: TESTING Y VALIDACIÓN

### 5.1 Crear tests unitarios

**Responsable:** Agente Especializado Testing

Archivos a crear/modificar:

- `tests/test_langchain_components.py`
- `tests/test_agent_deyy.py`
- `tests/test_divisor_chain.py`
- `tests/test_tools.py`
- `tests/test_integration_langchain.py`

### 5.2 Crear mocks para APIs externas

```python
# tests/mocks/mock_openai.py
# tests/mocks/mock_supabase.py
# tests/mocks/mock_postgres.py
```

### 5.3 Tests de integración completos

```python
async def test_full_langchain_integration():
    """
    Test completo: webhook -> unified_chain -> agent -> response
    """
    pass
```

---

## 🚀 FASE 6: CONFIGURACIÓN Y DESPLIEGUE

### 6.1 Actualizar `settings.py`

```python
class Settings(BaseSettings):
    # ... existente ...

    # LangChain
    LANGCHAIN_ENABLED: bool = True
    OPENAI_MODEL: str = "gpt-4"
    OPENAI_TEMPERATURE: float = 0.7

    # Supabase
    SUPABASE_URL: Optional[str] = None
    SUPABASE_SERVICE_KEY: Optional[str] = None

    # PostgreSQL Memory
    POSTGRES_MEMORY_TABLE: str = "langchain_memory"

    # MCP
    MCP_GOOGLE_CALENDAR_ENDPOINT: Optional[str] = None
```

### 6.2 Scripts de migración

**Archivo:** `migrations/001_langchain_tables.sql`

```sql
-- Tabla de memoria LangChain
CREATE TABLE IF NOT EXISTS langchain_memory (
    id SERIAL PRIMARY KEY,
    session_id VARCHAR(255) NOT NULL,
    message_type VARCHAR(50),
    content TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT NOW(),
    INDEX idx_session (session_id)
);

-- Tabla Supabase knowledge base
CREATE TABLE IF NOT EXISTS documents (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    content TEXT NOT NULL,
    metadata JSONB DEFAULT '{}',
    embedding VECTOR(1536)
);

-- Índice para búsqueda vectorial
CREATE INDEX ON documents USING ivfflat (embedding vector_cosine_ops);
```

### 6.3 Docker-compose actualizado

**Archivo:** `docker-compose.yml` (additions)

```yaml
services:
  postgres:
    environment:
      POSTGRES_DB: arcadium_automation

  supabase:
    # Config si se usa local
```

---

## 📝 FASE 7: DOCUMENTACIÓN

### 7.1 Actualizar README.md

Sección nueva: "LangChain Integration"

### 7.2 Documentación de APIs

- `docs/langchain_components.md`
- `docs/agents.md`
- `docs/tools.md`

### 7.3 Ejemplos de uso

```python
# examples/agent_usage.py
# examples/divisor_usage.py
# examples/knowledge_base.py
```

---

## 🔍 FASE 8: MONITOREO Y OBSERVABILIDAD

### 8.1 Métricas LangChain

**Responsable:** Agente Especializado Monitoring

Agregar métricas:

- Tiempos de respuesta por LLM
- Tokens utilizados
- Calls a herramientas
- Cache hit/miss ratio
- Tasa de error por agente

### 8.2 Logging estructurado

```python
logger = structlog.get_logger("langchain.deyy")
logger.info("Agent execution", tokens=150, tools_used=["search", "calendar"])
```

---

## 🎯 ORDEN DE IMPLEMENTACIÓN RECOMENDADO

### Semana 1: Fundaciones

1. **Día 1-2**: Instalar dependencias + Configurar entorno
2. **Día 3-4**: Implementar `LangChainComponentFactory` (utils/langchain_components.py)
3. **Día 5**: Testear componentes básicos (LLM, embeddings, memory)

### Semana 2: Herramientas y Agentes

4. **Día 1-2**: Implementar herramientas (utils/tools.py)
5. **Día 3-4**: Implementar Agente_Deyy (agents/arcadium_agent.py)
6. **Día 5**: Testear agente completo

### Semana 3: Cadenas Especializadas

7. **Día 1-3**: Implementar Divisor_Mensajes (chains/divisor_chain.py)
8. **Día 4-5**: Integrar en ArcadiumChainBuilder

### Semana 4: Integración y Testing

9. **Día 1-2**: Actualizar workflow executor para LangChain
10. **Día 3-4**: Tests de integración completos
11. **Día 5**: Migración DB + Configuración

### Semana 5: Producción

12. **Documentación completa**
13. **Load testing**
14. **Despliegue en producción**

---

## 📊 CRITERIOS DE ÉXITO

✅ **LangChain Components:**

- Todos los nodos LangChain del workflow están implementados
- Cada componente tiene tests unitarios (>80% cobertura)
- Componentes pueden usarse individualmente

✅ **Integración:**

- Unified chain ejecuta agente exitosamente
- Memoria se persiste en PostgreSQL
- Vector store busca knowledge base
- Herramientas funcionan correctamente

✅ **Performance:**

- Tiempo de respuesta < 5s (sin timeout)
- Memoria limpia automáticamente
- Cache de embeddings funcionando

✅ **Producción:**

- Logging estructurado completo
- Métricas Prometheus actualizadas
- Health checks funcionando
- Rollback automático en fallos

---

## 🚨 PROBLEMAS CONOCIDOS Y SOLUCIONES

| Problema                         | Causa                           | Solución                      |
| -------------------------------- | ------------------------------- | ----------------------------- |
| `ModuleNotFoundError: langchain` | Dependencia no instalada        | `pip install langchain`       |
| PostgreSQL memory falla          | Tabla no existe                 | Ejecutar migración SQL        |
| Supabase vector falla            | Extension pgvector no instalada | `CREATE EXTENSION vector;`    |
| Timeout en agentes               | Prompt muy largo                | Reducir `contextWindowLength` |
| MCP tool falla                   | MCP server no corriendo         | Iniciar `mcp-google-calendar` |

---

## 🔄 ROLLBACK PLAN

Si la implementación falla:

1. **Deshabilitar LangChain en settings:**

```python
LANGCHAIN_ENABLED = False
```

2. **Usar fallback al workflow original sin agentes** (ya implementado)

3. **Mantener tabs de datos** para análisis post-mortem

---

## 📞 PUNTOS DE CONTACTO Y ASIGNACIONES

**Agentes Especializados a crear:**

1. `langchain-base-agent` → Componentes base
2. `tools-agent` → Herramientas personalizadas
3. `agent-builder-agent` → Construcción de agentes
4. `chain-builder-agent` → Construcción de cadenas
5. `integration-agent` → Integración con sistema existente
6. `testing-agent` → Tests y validación
7. `migration-agent` → Migraciones DB
8. `documentation-agent` → Docs y ejemplos

---

## ✅ CHECKLIST DE IMPLEMENTACIÓN

- [ ] Fase 1: Dependencias instaladas
- [ ] Fase 2.1: utils/langchain_components.py implementado
- [ ] Fase 2.2: utils/tools.py implementado completo
- [ ] Fase 2.3: agents/arcadium_agent.py implementado
- [ ] Fase 2.4: chains/divisor_chain.py implementado
- [ ] Fase 3: ArcadiumChainBuilder actualizado
- [ ] Fase 4: Todos los nodos LangChain mapeados
- [ ] Fase 5: Tests unitarios (>80% coverage)
- [ ] Fase 6: Migraciones SQL aplicadas
- [ ] Fase 7: Documentación actualizada
- [ ] Fase 8: Métricas y logging completo

---

**Total estimado:** 5 semanas (20 días hábiles)
**Complejidad:** Alta
**Riesgo:** Medio (tiene rollback plan)
