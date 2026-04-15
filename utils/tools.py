# -*- coding: utf-8 -*-
"""
Herramientas personalizadas para Agente Deyy
Herramientas LangChain específicas del dominio Arcadium
"""

from typing import Any, Dict, Optional, List
import asyncio
import json
import subprocess
from agents.langchain_compat import BaseTool, StructuredTool, tool
from langchain_core.callbacks import CallbackManagerForToolRun
from pydantic import BaseModel, Field
import structlog
from core.config import settings
from utils.langchain_components import LangChainComponentFactory

# Importar herramientas de Arcadium (al final para evitar circulares)
# Se importan dinámicamente en get_deyy_tools

logger = structlog.get_logger("tools")


# ========== TOOL SCHEMAS ==========

class PlanningInput(BaseModel):
    """Input para PlanningTool"""
    task: str = Field(description="Tarea o problema a planificar")
    constraints: Optional[str] = Field(None, description="Restricciones o consideraciones")
    max_steps: int = Field(default=10, description="Máximo número de pasos", ge=1, le=50)


class ThinkInput(BaseModel):
    """Input para ThinkTool"""
    thought: str = Field(description="Pensamiento o razonamiento a estructurar")
    context: Optional[str] = Field(None, description="Contexto adicional")
    focus_areas: Optional[List[str]] = Field(None, description="Áreas a considerar")


class CalendarInput(BaseModel):
    """Input para MCPGoogleCalendarTool"""
    action: str = Field(description="Acción: list/create/update/delete")
    calendar_id: Optional[str] = Field(None, description="ID del calendario")
    event_id: Optional[str] = Field(None, description="ID del evento (para update/delete)")
    title: Optional[str] = Field(None, description="Título del evento")
    description: Optional[str] = Field(None, description="Descripción del evento")
    start_time: Optional[str] = Field(None, description="Inicio ISO 8601")
    end_time: Optional[str] = Field(None, description="Fin ISO 8601")
    attendees: Optional[List[str]] = Field(None, description="Lista de asistentes")


class KnowledgeSearchInput(BaseModel):
    """Input para KnowledgeBaseSearch"""
    query: str = Field(description="Consulta de búsqueda")
    k: int = Field(default=5, description="Número de resultados", ge=1, le=20)
    similarity_threshold: float = Field(default=0.7, description="Umbral de similitud", ge=0, le=1)


# ========== HERRAMIENTAS PERSONALIZADAS ==========

class PlanningTool(BaseTool):
    """
    Planificador Obligatorio - Herramienta para descomponer tareas

    Equivalente a: @n8n/n8n-nodes-langchain.toolCode
    """

    name: str = "planificador_obligatorio"
    description: str = """
    Planifica tareas complejas descomponiéndolas en pasos ejecutables.
    Útil para proyectos grandes, implementaciones, o cualquier cosa que requiera múltiples pasos.

    Entrada:
    - task: La tarea principal a planificar
    - constraints: Restricciones o consideraciones (opcional)
    - max_steps: Máximo número de pasos (1-50)

    Salida:
    Plan estructurado con pasos secuenciales, responsables, y estimaciones.
    """

    args_schema: Optional[BaseModel] = PlanningInput

    def _run(
        self,
        task: str,
        constraints: Optional[str] = None,
        max_steps: int = 10,
        run_manager: Optional[CallbackManagerForToolRun] = None
    ) -> Dict[str, Any]:
        """
        Ejecuta planificación de tarea

        Args:
            task: Descripción de la tarea
            constraints: Restricciones
            max_steps: Máximo pasos

        Returns:
            Dict con plan estructurado
        """
        logger.info(
            "Ejecutando PlanningTool",
            task=task[:100],
            constraints=constraints,
            max_steps=max_steps
        )

        # Aquí se ejecutaría código JS (sandboxed) o usar LLM interno
        # Por ahora, implementación simple
        plan = {
            "task": task,
            "constraints": constraints or "Ninguna",
            "steps": [],
            "estimated_time": "TBD",
            "dependencies": []
        }

        # Placeholder: dividir en pasos lógicos
        # TODO: Integrar con LLM para generar plan real
        steps = [
            {"step": 1, "description": "Analizar requisitos", "estimated": "1h"},
            {"step": 2, "description": "Diseñar solución", "estimated": "2h"},
            {"step": 3, "description": "Implementar", "estimated": "4h"},
            {"step": 4, "description": "Testear", "estimated": "2h"},
            {"step": 5, "description": "Desplegar", "estimated": "1h"}
        ]

        plan["steps"] = steps[:max_steps]
        plan["estimated_time"] = sum([2, 2, 4, 2, 1][:max_steps])

        return {
            "status": "success",
            "plan": plan,
            "total_steps": len(plan["steps"]),
            "tool": self.name
        }

    async def _arun(
        self,
        task: str,
        constraints: Optional[str] = None,
        max_steps: int = 10
    ) -> Dict[str, Any]:
        """Async version"""
        return self._run(task, constraints, max_steps)


class ThinkTool(BaseTool):
    """
    Tool Think - Razonamiento estructurado

    Equivalente a: @n8n/n8n-nodes-langchain.toolThink
    """

    name: str = "think"
    description: str = """
    Herramienta para razonar profundamente sobre un problema.
    Útil para:
    - Analizar situaciones complejas
    - Evaluar múltiples opciones
    - Identificar riesgos y consideraciones
    - Estructurar pensamiento lógico

    Entrada:
    - thought: El pensamiento o problema a razonar
    - context: Contexto adicional (opcional)
    - focus_areas: Áreas específicas a considerar (opcional)

    Salida:
    Razonamiento estructurado con análisis, implicaciones, y conclusiones.
    """

    args_schema: Optional[BaseModel] = ThinkInput

    def _run(
        self,
        thought: str,
        context: Optional[str] = None,
        focus_areas: Optional[List[str]] = None,
        run_manager: Optional[CallbackManagerForToolRun] = None
    ) -> str:
        """
        Ejecuta razonamiento estructurado

        Returns:
            Texto con razonamiento completo
        """
        logger.info(
            "Ejecutando ThinkTool",
            thought_length=len(thought),
            focus_areas=focus_areas
        )

        # Implementación: estructurar pensamiento
        reasoning = f"""
RAZONAMIENTO ESTRUCTURADO
===========================

PROBLEMA:
{thought}

CONTEXTO:
{context or 'No especificado'}

FOCUS AREAS:
{', '.join(focus_areas) if focus_areas else 'General'}

ANÁLISIS:
1. ¿Cuál es el problema real?
   - Identificar causa raíz
   - Separar síntomas de problema

2. ¿Qué información tengo?
   - Datos disponibles
   - Datos faltantes
   - Supuestos

3. ¿Qué opciones hay?
   - Múltiples soluciones posibles
   - Pros y contras de cada una

4. ¿Qué riesgos hay?
   - Posibles fallos
   - Impacto de errores
   - Mitigaciones

5. ¿Qué he decidido?
   - Razón de la decisión
   - Alternativas descartadas
   - Próximos pasos

IMPLICACIONES:
- Impacto en sistema existente
- Recursos necesarios
- Tiempo de implementación

CONCLUSIÓN:
La mejor aproximación después de analizar el problema es...
        """.strip()

        return reasoning

    async def _arun(
        self,
        thought: str,
        context: Optional[str] = None,
        focus_areas: Optional[List[str]] = None
    ) -> str:
        """Async version"""
        return self._run(thought, context, focus_areas)


class KnowledgeBaseSearch(BaseTool):
    """
    Búsqueda en knowledge base vectorial (Supabase)

    Equivalente a: @n8n/n8n-nodes-langchain.vectorStoreSupabase
    """

    name: str = "knowledge_base_search"
    description: str = """
    Busca información en la base de conocimientos usando búsqueda semántica.
    Ideal para encontrar documentación, respuestas frecuentes, o referencia técnica.

    Entrada:
    - query: Consulta de búsqueda
    - k: Número de resultados (1-20, default 5)
    - similarity_threshold: Umbral mínimo de similitud (0-1, default 0.7)

    Salida:
    Lista de documentos relacionados con puntuaciones de similitud.
    """

    args_schema: Optional[BaseModel] = KnowledgeSearchInput
    vectorstore: Optional[Any] = None  # Campo para inyección de dependencia

    def __init__(self, vectorstore: Any = None, **kwargs):
        super().__init__(**kwargs)
        if vectorstore:
            self.vectorstore = vectorstore
        else:
            # Crear vectorstore por defecto si no se provee
            try:
                self.vectorstore = LangChainComponentFactory.create_supabase_vectorstore()
            except Exception as e:
                logger.warning("No se pudo crear vectorstore", error=str(e))
                self.vectorstore = None

    def _run(
        self,
        query: str,
        k: int = 5,
        similarity_threshold: float = 0.7,
        run_manager: Optional[CallbackManagerForToolRun] = None
    ) -> Dict[str, Any]:
        """
        Busca en vector store

        Returns:
            Dict con documentos y scores
        """
        if not self.vectorstore:
            return {
                "status": "error",
                "error": "Vector store no disponible",
                "documents": []
            }

        logger.info(
            "Buscando en knowledge base",
            query=query[:100],
            k=k,
            threshold=similarity_threshold
        )

        try:
            # Búsqueda similitud
            docs = self.vectorstore.similarity_search_with_relevance_scores(
                query=query,
                k=k
            )

            # Filtrar por umbral
            filtered = [
                {
                    "content": doc.page_content,
                    "metadata": doc.metadata,
                    "score": score
                }
                for doc, score in docs
                if score >= similarity_threshold
            ]

            return {
                "status": "success",
                "query": query,
                "total_results": len(filtered),
                "documents": filtered
            }

        except Exception as e:
            logger.error("Error en knowledge search", error=str(e))
            return {
                "status": "error",
                "error": str(e),
                "documents": []
            }

    async def _arun(
        self,
        query: str,
        k: int = 5,
        similarity_threshold: float = 0.7
    ) -> Dict[str, Any]:
        """Async version"""
        # Vector store operations son async-ready en langchain
        return self._run(query, k, similarity_threshold)


class MCPGoogleCalendarTool(BaseTool):
    """
    Integración con Google Calendar via servicio directo (no MCP)

    NOTA: Esta herramienta usa GoogleCalendarService directamente.
    Para verdadero MCP, se necesita un servidor MCP de Google Calendar corriendo.
    """

    name: str = "google_calendar"
    description: str = """
    Interactúa con Google Calendar para gestionar eventos de citas.

    Operaciones:
    - list_events: Lista eventos en rango de fechas
    - create_event: Crea nuevo evento
    - update_event: Modifica evento existente
    - delete_event: Elimina evento
    - get_available_slots: Obtiene horarios libres en una fecha
    - check_availability: Verifica si un slot específico está libre

    Selecciona la acción y completa los campos requeridos.
    """

    args_schema: Optional[BaseModel] = CalendarInput
    calendar_service: Optional[Any] = None

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        try:
            from services.google_calendar_service import get_default_calendar_service
            self.calendar_service = get_default_calendar_service()
        except Exception as e:
            logger.warning("No se pudo inicializar Google Calendar service", error=str(e))
            self.calendar_service = None

    def _run(
        self,
        action: str,
        calendar_id: Optional[str] = None,
        event_id: Optional[str] = None,
        title: Optional[str] = None,
        description: Optional[str] = None,
        start_time: Optional[str] = None,
        end_time: Optional[str] = None,
        attendees: Optional[List[str]] = None,
        run_manager: Optional[CallbackManagerForToolRun] = None
    ) -> Dict[str, Any]:
        """
        Ejecuta operación en Google Calendar

        Returns:
            Dict con resultado
        """
        if not self.calendar_service:
            return {
                "success": False,
                "error": "Google Calendar service no disponible. Verifica credenciales y configuración.",
                "action": action
            }

        logger.info(
            "Ejecutando Google Calendar",
            action=action,
            calendar=calendar_id,
            event_id=event_id
        )

        try:
            # Run async sync
            import asyncio
            try:
                loop = asyncio.get_event_loop()
            except RuntimeError:
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)

            result = loop.run_until_complete(
                self._arun(action, calendar_id, event_id, title, description,
                          start_time, end_time, attendees)
            )
            return result

        except Exception as e:
            logger.error("Error en Google Calendar tool", action=action, error=str(e))
            return {
                "success": False,
                "error": str(e),
                "action": action
            }

    async def _arun(
        self,
        action: str,
        calendar_id: Optional[str] = None,
        event_id: Optional[str] = None,
        title: Optional[str] = None,
        description: Optional[str] = None,
        start_time: Optional[str] = None,
        end_time: Optional[str] = None,
        attendees: Optional[List[str]] = None
    ) -> Dict[str, Any]:
        """Async version"""
        from datetime import datetime
        from zoneinfo import ZoneInfo

        # Convertir fechas ISO a datetime si se proporcionan
        start_dt = None
        end_dt = None
        if start_time:
            try:
                start_dt = datetime.fromisoformat(start_time.replace('Z', '+00:00'))
            except Exception:
                return {"success": False, "error": f"Formato start_time inválido: {start_time}"}
        if end_time:
            try:
                end_dt = datetime.fromisoformat(end_time.replace('Z', '+00:00'))
            except Exception:
                return {"success": False, "error": f"Formato end_time inválido: {end_time}"}

        try:
            if action == "list_events":
                if not start_dt or not end_dt:
                    return {"success": False, "error": "start_time y end_time requeridos para list_events"}
                events = await self.calendar_service.list_events(start_dt, end_dt)
                return {
                    "success": True,
                    "action": action,
                    "events": events,
                    "count": len(events)
                }

            elif action == "create_event":
                if not title or not start_dt or not end_dt:
                    return {"success": False, "error": "title, start_time y end_time requeridos para create_event"}
                event = await self.calendar_service.create_event(
                    title=title,
                    start_time=start_dt,
                    end_time=end_dt,
                    description=description or "",
                    attendees=attendees
                )
                return {
                    "success": True,
                    "action": action,
                    "event": event,
                    "event_id": event.get('id'),
                    "html_link": event.get('htmlLink')
                }

            elif action == "update_event":
                if not event_id:
                    return {"success": False, "error": "event_id requerido para update_event"}
                event = await self.calendar_service.update_event(
                    event_id=event_id,
                    title=title,
                    start_time=start_dt,
                    end_time=end_dt,
                    description=description,
                    attendees=attendees
                )
                return {
                    "success": True,
                    "action": action,
                    "event": event,
                    "event_id": event_id
                }

            elif action == "delete_event":
                if not event_id:
                    return {"success": False, "error": "event_id requerido para delete_event"}
                success = await self.calendar_service.delete_event(event_id)
                return {
                    "success": success,
                    "action": action,
                    "event_id": event_id
                }

            elif action == "get_available_slots":
                if not start_dt:
                    return {"success": False, "error": "start_time (fecha) requerido para get_available_slots"}
                duration = 60  # default
                slots = await self.calendar_service.get_available_slots(
                    date=start_dt,
                    duration_minutes=duration,
                    start_hour=9,
                    end_hour=18
                )
                return {
                    "success": True,
                    "action": action,
                    "date": start_dt.date().isoformat(),
                    "duration_minutes": duration,
                    "slots": [
                        {"start": s["start"].isoformat(), "end": s["end"].isoformat()}
                        for s in slots
                    ],
                    "count": len(slots)
                }

            elif action == "check_availability":
                if not start_dt or not end_dt:
                    return {"success": False, "error": "start_time y end_time requeridos para check_availability"}
                available = await self.calendar_service.check_availability(start_dt, end_dt)
                return {
                    "success": True,
                    "action": action,
                    "available": available,
                    "start_time": start_dt.isoformat(),
                    "end_time": end_dt.isoformat()
                }

            else:
                return {
                    "success": False,
                    "error": f"Acción no reconocida: {action}",
                    "valid_actions": ["list_events", "create_event", "update_event", "delete_event", "get_available_slots", "check_availability"]
                }

        except Exception as e:
            logger.error("Error ejecutando acción Google Calendar", action=action, error=str(e))
            return {
                "success": False,
                "error": str(e),
                "action": action
            }


# ========== FACTORY DE HERRAMIENTAS ==========

def get_deyy_tools(
    vectorstore: Any = None,
    llm: Any = None,
    db_session: Optional[Any] = None
) -> List[BaseTool]:
    """
    Retorna lista de herramientas para Agente Deyy

    Args:
        vectorstore: Vector store para knowledge base
        llm: LLM para herramientas que lo necesiten
        db_session: Sesión de SQLAlchemy para herramientas que acceden a DB

    Returns:
        Lista de herramientas configuradas
    """
    tools = []

    # 1. Planning Tool (Planificador_Obligatorio)
    tools.append(PlanningTool())

    # 2. Think Tool
    tools.append(ThinkTool())

    # 3. Knowledge Base Search (Supabase_KnowledgeBase)
    tools.append(KnowledgeBaseSearch(vectorstore=vectorstore))

    # 4. MCP Google Calendar (MCP_GoogleCalendar)
    tools.append(MCPGoogleCalendarTool())

    # 5. Herramientas específicas de Arcadium (citas, WhatsApp, perfiles)
    try:
        from utils.arcadium_tools import get_arcadium_tools
        arcadium_tools = get_arcadium_tools(db_session=db_session)
        tools.extend(arcadium_tools)
        logger.info("Herramientas Arcadium agregadas", count=len(arcadium_tools))
    except ImportError as e:
        logger.warning("No se pudieron cargar herramientas Arcadium", error=str(e))

    # TODO: Agregar más herramientas según necesidades

    logger.info("Herramientas Deyy creadas", total=len(tools))
    return tools


def get_all_tools() -> List[Dict[str, Any]]:
    """
    Listametadata de todas las herramientas disponibles

    Returns:
        Lista de dicts con info de cada herramienta
    """
    return [
        {
            "name": "planificador_obligatorio",
            "description": "Planifica tareas complejas en pasos ejecutables",
            "type": "toolCode",
            "class": PlanningTool
        },
        {
            "name": "think",
            "description": "Razona sobre problemas antes de actuar",
            "type": "toolThink",
            "class": ThinkTool
        },
        {
            "name": "knowledge_base_search",
            "description": "Busca en knowledge base (Supabase vector store)",
            "type": "toolVector",
            "class": KnowledgeBaseSearch
        },
        {
            "name": "mcp_google_calendar",
            "description": "Interactúa con Google Calendar",
            "type": "mcpClientTool",
            "class": MCPGoogleCalendarTool
        }
    ]
