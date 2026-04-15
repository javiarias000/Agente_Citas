#!/usr/bin/env python3
"""
Orquestador principal de Arcadium
Sin dependencia en n8n - comunicación directa con WhatsApp API

FIXES APLICADOS:
- [CRÍTICO] timedelta no importado → agregado al import de datetime
- [CRÍTICO] process_message recibía context_vars pero ArcadiumAgent no lo acepta → eliminado
- [CRÍTICO] _handle_whatsapp_webhook accedía a result["response"] como dict
  pero ArcadiumAgent retorna AgentResponse → normalización igual que Chatwoot
- [CRÍTICO] ChatwootMessage sin sender_type="agent" → puede romper anti-loop
- [CRÍTICO] _handle_chatwoot_webhook sin timeout → hangs infinitos posibles
- [MEDIO]  Check redundante de sender_type después de parse_webhook_payload → eliminado
- [MEDIO]  _agents cache sin límite → agregado LRU con max 500 sesiones
- [MEDIO]  process_webhook y WebSocket usaban result.get() sobre AgentResponse → corregido
"""

import asyncio
import json
import os

# Cargar .env antes de cualquier import de LangChain/LangSmith.
# LangChain lee LANGCHAIN_TRACING_V2 y LANGCHAIN_API_KEY al importarse;
# si load_dotenv() corre después, el tracing no se activa.
from dotenv import load_dotenv
load_dotenv()
import uuid
from collections import OrderedDict
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

import structlog
from fastapi import (
    FastAPI,
    Header,
    HTTPException,
    Request,
    WebSocket,
    WebSocketDisconnect,
)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse
from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from core.config import get_settings
from db.models import Base, Conversation, Message, Project, ProjectAgentConfig
from memory.memory_manager import MemoryManager
from services.chatwoot_service import ChatwootError, ChatwootMessage, ChatwootService
from services.whatsapp_service import WhatsAppError, WhatsAppMessage, WhatsAppService
from utils.logger import setup_logger
from utils.phone_utils import normalize_phone

logger = structlog.get_logger("orchestrator")


# ============================================
# Helpers
# ============================================


def _normalize_agent_result(result: Any) -> Dict[str, Any]:
    """
    Normaliza el resultado del agente a un dict uniforme.

    ArcadiumAgent retorna AgentResponse (objeto).
    DeyyAgent/otros retornan dict.
    """
    if hasattr(result, "text"):
        # AgentResponse object
        return {
            "response": result.text,
            "status": result.status,
            "tool_calls": [],
            "execution_time_seconds": 0,
            "session_id": getattr(result, "session_id", None),
        }
    # Dict legacy
    return {
        "response": result.get("response", ""),
        "status": result.get("status", "ok"),
        "tool_calls": result.get("tool_calls", []),
        "execution_time_seconds": result.get("execution_time_seconds", 0),
        "session_id": result.get("session_id"),
    }


# ============================================
# Database Setup
# ============================================


class Database:
    """Gestor de base de datos"""

    def __init__(self, url: str):
        if "+asyncpg" not in url and "+psycopg" not in url:
            url = url.replace("postgresql://", "postgresql+asyncpg://", 1)
        self.engine = create_async_engine(
            url, echo=False, pool_size=10, max_overflow=20
        )
        self.async_session_maker = sessionmaker(
            self.engine, class_=AsyncSession, expire_on_commit=False
        )

    async def init(self):
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        logger.info("Base de datos inicializada")

    def get_session(self) -> AsyncSession:
        return self.async_session_maker()


# ============================================
# FastAPI App
# ============================================


class ArcadiumAPI:
    """
    API principal de Arcadium.
    Maneja webhooks de WhatsApp y Chatwoot y los enruta al agente correcto.
    """
    
    # FIX: límite máximo de agentes en cache (LRU)
    _AGENT_CACHE_MAX = 500

    def __init__(self, settings=None):
        self.settings = settings or get_settings()
        self.db: Optional[Database] = None
        self.memory_manager: Optional[MemoryManager] = None
        self.whatsapp_service: Optional[WhatsAppService] = None
        self.chatwoot_service: Optional[ChatwootService] = None

        from services.composio_calendar_service import ComposioCalendarService

        # Instanciar servicios de calendario para ambos doctores (initialize() async se llama en _init_langgraph)
        JORGE_EMAIL = "jorge.arias.amauta@gmail.com"
        JAVIER_EMAIL = "javiarias000@gmail.com"
        self._calendar_services: Dict[str, Any] = {}

        try:
            jorge_svc = ComposioCalendarService(
                calendar_id=JORGE_EMAIL,
                timezone=self.settings.GOOGLE_CALENDAR_TIMEZONE,
            )
            javier_svc = ComposioCalendarService(
                calendar_id=JAVIER_EMAIL,
                timezone=self.settings.GOOGLE_CALENDAR_TIMEZONE,
            )
            self._calendar_services = {
                JORGE_EMAIL: jorge_svc,
                JAVIER_EMAIL: javier_svc,
            }
            logger.info("ComposioCalendarService instanciado para 2 doctores (pendiente initialize())")
        except Exception as e:
            logger.error("Error instanciando ComposioCalendarService", error=str(e))

        # FIX: OrderedDict para implementar LRU simple en el cache de agentes
        self._agents: OrderedDict[str, Any] = OrderedDict()

        self.default_project_id: Optional[uuid.UUID] = None

        # Rate limiting: phone -> list of timestamps
        self._rate_limit: Dict[str, list] = {}
        self._rate_limit_max = 30  # max mensajes por minuto por phone

        # Session locks: serializa requests concurrentes del mismo número.
        # Sin esto, dos mensajes del mismo usuario que llegan con <2s de diferencia
        # se procesan en paralelo, leen el mismo estado, y sobreescriben datos.
        self._session_locks: Dict[str, asyncio.Lock] = {}

        # Stores para LangGraph (se inicializan en _init_langgraph)
        self.state_store = None  # Store para historial y estado (BaseStore)
        self.vector_store = (
            None  # Store vectorial para memorias semánticas (langgraph BaseStore)
        )

        logger.info("ArcadiumAPI creada")

    # ============================================
    # Inicialización
    # ============================================

    async def initialize(self):
        logger.info("Inicializando ArcadiumAPI")

        setup_logger(self.settings.LOG_LEVEL)

        self.db = Database(self.settings.DATABASE_URL)
        await self.db.init()

        from db import init_session_maker

        init_session_maker(self.db.engine)

        await self._run_migrations()
        await self._load_default_project()

        self.memory_manager = MemoryManager(self.settings)
        await self.memory_manager.initialize()

        from core.store import ArcadiumStore

        self.store = ArcadiumStore(self.memory_manager)

        self.whatsapp_service = WhatsAppService(self.settings)

        if self.settings.CHATWOOT_API_URL and self.settings.CHATWOOT_API_TOKEN:
            self.chatwoot_service = ChatwootService(self.settings)
            logger.info("ChatwootService inicializado")
        else:
            logger.info("Chatwoot no configurado (settings faltantes)")

        if self.settings.ENABLE_METRICS:
            self._setup_metrics()

        if self.settings.USE_LANGGRAPH:
            await self._init_langgraph()

        logger.info("ArcadiumAPI inicializada")

    def _setup_metrics(self):
        try:
            from prometheus_client import start_http_server

            start_http_server(self.settings.METRICS_PORT)
            logger.info(
                "Métricas Prometheus iniciadas", port=self.settings.METRICS_PORT
            )
        except ImportError:
            logger.warning("prometheus_client no instalado, métricas deshabilitadas")

    async def _init_langgraph(self) -> None:
        try:
            from langchain_openai import ChatOpenAI

            from memory_agent_integration.memory_agent_backend import MemoryAgentBackend
            from src.graph import compile_graph
            from src.graph_v2 import compile_graph_v2
            from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver as PostgresSaver
            from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer


            # 1. Backend unificado: historial secuencial + vector store semántico.
            #    Reemplaza el PostgresStore + vector_store separados anteriores.
            self.state_store = MemoryAgentBackend(
                settings=self.settings,
                engine=self.db.engine,
            )
            await self.state_store.initialize()

            # Exponer el vector store interno para los nodos del grafo
            self.vector_store = (
                self.state_store.store if self.settings.USE_MEMORY_AGENT else None
            )

            # 2. LLM
            self.langgraph_llm = ChatOpenAI(
                model=self.settings.LANGGRAPH_MODEL,
                temperature=self.settings.LANGGRAPH_TEMPERATURE,
            )

            # 3. Compilar grafo
            # AsyncPostgresSaver requiere psycopg3 (postgresql+psycopg://...)
            # 3. Compilar grafo

            pg_url = self.settings.DATABASE_URL

            # limpiar drivers
            pg_url = pg_url.replace("postgresql+asyncpg://", "postgresql://")
            pg_url = pg_url.replace("postgresql+psycopg2://", "postgresql://")
            pg_url = pg_url.replace("postgresql+psycopg://", "postgresql://")

            # SSL para Supabase
            if "supabase.co" in pg_url:
                if "sslmode=" not in pg_url:
                    pg_url += "?sslmode=require"

            # abrir conexión correctamente
            _serde = JsonPlusSerializer(
                allowed_msgpack_modules=[("asyncpg.pgproto.pgproto", "UUID")]
            )
            self.checkpointer_ctx = PostgresSaver.from_conn_string(pg_url, serde=_serde)
            self.checkpointer = await self.checkpointer_ctx.__aenter__()

            # Inicializar AMBOS ComposioCalendarService (async — carga tools MCP)
            wrapped_calendars: Dict[str, Any] = {}
            for email, svc in self._calendar_services.items():
                try:
                    await svc.initialize()
                    from src.calendar_service import GoogleCalendarService as CalendarAdapter
                    wrapped_calendars[email] = CalendarAdapter(calendar_service=svc, db_service=None)
                    logger.info("CalendarAdapter inicializado", doctor=email)
                except Exception as e:
                    logger.error("Error inicializando calendar para doctor", doctor=email, error=str(e))

            # Instanciar AppointmentService para persistencia en DB
            try:
                from services.appointment_service import AppointmentService
                _db_service = AppointmentService()
                logger.info("AppointmentService instanciado para db_service")
            except Exception as e:
                logger.warning("No se pudo instanciar AppointmentService, db_service=None", error=str(e))
                _db_service = None

            # Actualizar wrapped calendars con db_service
            for email in wrapped_calendars:
                wrapped_calendars[email]._db = _db_service

            # Default: usar primer disponible o None
            default_email = self.settings.GOOGLE_CALENDAR_DEFAULT_ID
            default_wrapped = wrapped_calendars.get(default_email) or (
                next(iter(wrapped_calendars.values()), None) if wrapped_calendars else None
            )

            # compilar grafo (V2 = ReAct 5 nodos; V1 = state machine 20+ nodos)
            if self.settings.USE_GRAPH_V2:
                self.langgraph_graph = compile_graph_v2(
                    llm=self.langgraph_llm,
                    store=self.state_store,
                    calendar_service=default_wrapped,
                    calendar_services=wrapped_calendars,
                    db_service=_db_service,
                    checkpointer=self.checkpointer,
                )
                logger.info("Usando Graph V2 (ReAct, 5 nodos)")
            else:
                self.langgraph_graph = compile_graph(
                    llm=self.langgraph_llm,
                    store=self.state_store,
                    vector_store=self.vector_store,
                    calendar_service=default_wrapped,
                    calendar_services=wrapped_calendars,
                    db_service=_db_service,
                    checkpointer=self.checkpointer,
                )
                logger.info("Usando Graph V1 (state machine, 20+ nodos)")

            logger.info(
                "LangGraph components inicializados",
                state_store=type(self.state_store).__name__,
                vector_store=type(self.vector_store).__name__
                if self.vector_store
                else None,
            )
        except Exception as e:
            logger.error("Error inicializando LangGraph", error=str(e), exc_info=True)
            raise

    async def _create_langgraph_agent(
        self,
        session_id: str,
        project_id: Optional[uuid.UUID],
    ) -> Any:
        from src.agent import ArcadiumAgent

        # memory_integration activa la búsqueda semántica por turno.
        # Solo se pasa cuando USE_MEMORY_AGENT=True (feature flag).
        memory_integration = (
            self.state_store if self.settings.USE_MEMORY_AGENT else None
        )

        return ArcadiumAgent(
            session_id=f"deyy_{session_id}",
            graph=self.langgraph_graph,
            store=self.state_store,
            llm=self.langgraph_llm,
            project_id=project_id,
            memory_integration=memory_integration,
        )

    async def _load_default_project(self) -> None:
        logger.info("Cargando proyecto por defecto")
        try:
            from db import get_async_session

            async with get_async_session() as session:
                stmt = select(Project).where(Project.is_active == True).limit(1)
                result = await session.execute(stmt)
                project = result.scalar_one_or_none()
                if project:
                    self.default_project_id = project.id
                    logger.info(
                        "Proyecto por defecto cargado",
                        project_id=str(project.id),
                        project_name=project.name,
                    )
                else:
                    logger.warning("No hay proyectos activos en DB")
                    self.default_project_id = None
        except Exception as e:
            logger.error(
                "Error cargando proyecto por defecto", error=str(e), exc_info=True
            )
            self.default_project_id = None

    async def _run_migrations(self):
        logger.info("Running database migrations...")
        try:
            from db.migrate import run_migrations_sync

            await asyncio.to_thread(run_migrations_sync)
            logger.info("Migrations completed successfully")
        except ImportError as e:
            logger.warning("Migration script not available", error=str(e))
        except Exception as e:
            logger.error("Migration failed", error=str(e))
            raise

    # ============================================
    # FastAPI App
    # ============================================

    def create_app(self) -> FastAPI:

        @asynccontextmanager
        async def lifespan(app: FastAPI):
            try:
                await self.initialize()
                app.state.api = self
                logger.info("✅ Servidor iniciado correctamente")
            except Exception as e:
                logger.error("❌ Error iniciando servidor", error=str(e), exc_info=True)
                raise
            yield
            try:
                await self.shutdown()
            except Exception as e:
                logger.error("Error cerrando servidor", error=str(e))

        app = FastAPI(
            title=self.settings.APP_NAME,
            version="1.0.0",
            lifespan=lifespan,
            debug=self.settings.DEBUG,
        )

        app.add_middleware(
            CORSMiddleware,
            allow_origins=self.settings.CORS_ORIGINS,
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
        )

        @app.middleware("http")
        async def log_requests(request: Request, call_next):
            start_time = datetime.utcnow()
            headers = dict(request.headers)
            if "x-api-key" in headers:
                headers["x-api-key"] = "***REDACTED***"
            logger.info(
                "Request started",
                method=request.method,
                url=str(request.url),
                client=request.client.host if request.client else None,
            )
            try:
                response = await call_next(request)
                elapsed = (datetime.utcnow() - start_time).total_seconds() * 1000
                logger.info(
                    "Request completed",
                    method=request.method,
                    url=str(request.url),
                    status_code=response.status_code,
                    elapsed_ms=round(elapsed, 2),
                )
                return response
            except HTTPException as e:
                elapsed = (datetime.utcnow() - start_time).total_seconds() * 1000
                logger.warning(
                    "Request HTTP error",
                    status_code=e.status_code,
                    detail=str(e.detail)[:200],
                    elapsed_ms=round(elapsed, 2),
                )
                raise
            except Exception as e:
                elapsed = (datetime.utcnow() - start_time).total_seconds() * 1000
                logger.error(
                    "Request error",
                    error=str(e)[:500],
                    elapsed_ms=round(elapsed, 2),
                    exc_info=True,
                )
                raise

        # Static files
        from fastapi.staticfiles import StaticFiles

        static_dir = os.path.join(os.path.dirname(__file__), "..", "static")
        if os.path.exists(static_dir):
            app.mount("/static", StaticFiles(directory=static_dir), name="static")

        # ── OAuth2 ──────────────────────────────────────

        from fastapi.responses import RedirectResponse

        @app.get("/auth/google")
        async def auth_google():
            return JSONResponse(
                {
                    "info": "La autenticación usa refresh token desde variables de entorno.",
                    "required_env_vars": ["GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET", "GOOGLE_REFRESH_TOKEN"],
                }
            )

        @app.get("/oauth2callback")
        async def oauth2callback(code: str = None):
            return JSONResponse(
                {"info": "Este callback ya no se usa. La autenticación es via refresh token en .env."}
            )

        # ── Endpoints de información ──────────────────

        @app.post("/api/agent/review")
        async def agent_review(request: Request):
            """
            Endpoint para aprobar/editar/rechazar acciones del agente.
            Payload: { "session_id": "...", "decisions": [{"type": "approve"}] }
            """
            try:
                data = await request.json()
                session_id = data.get("session_id")
                decisions = data.get("decisions")

                if not session_id or not decisions:
                    raise HTTPException(status_code=400, detail="Missing session_id or decisions")

                # Recuperar agente del cache
                # El session_id en el cache puede tener el prefijo 'deyy_' o no dependiendo de cómo se guardó
                cache_key = session_id
                if cache_key not in self._agents:
                    # Intentar buscar con prefijo si es un teléfono
                    alt_key = f"deyy_{session_id}" if not session_id.startswith("deyy_") else session_id
                    if alt_key in self._agents:
                        cache_key = alt_key
                    else:
                        # Si no está en cache, intentar recrearlo (aunque perderíamos estado si no es persistente)
                        # Pero LangGraph usa checkpointer, así que podemos recrear el agente y el grafo cargará el estado.
                        logger.info("Agente no en cache, recreando...", session_id=session_id)
                        agent = await self._get_or_create_agent(session_id=session_id)
                        self._agents[cache_key] = agent
                else:
                    agent = self._agents.get(cache_key)

                if not agent:
                    raise HTTPException(status_code=404, detail="Agent not found in cache")

                from langgraph.types import Command

                # Configuración del thread para LangGraph
                config = {"configurable": {"thread_id": agent.session_id}}

                # Ejecutar el grafo resumiendo desde la interrupción
                result = await agent.graph.ainvoke(
                    Command(resume={"decisions": decisions}),
                    config=config
                )

                # Normalizar resultado
                from core.orchestrator import _normalize_agent_result
                final_result = _normalize_agent_result(result)

                # Enviar respuesta al usuario (WhatsApp/Chatwoot)
                # Necesitamos saber la plataforma. Buscamos la conversación en DB.
                async with self.db.get_session() as session:
                    from db.models import Conversation
                    from sqlalchemy import select
                    stmt = select(Conversation).where(Conversation.phone_number == session_id.replace("deyy_", ""))
                    conv = (await session.execute(stmt)).scalar_one_or_none()

                    if conv and conv.platform == "whatsapp":
                        await self.whatsapp_service.send_message(
                            WhatsAppMessage(to=conv.phone_number, text=final_result["response"])
                        )
                    elif conv and conv.platform == "chatwoot":
                        # Lógica simplificada de Chatwoot
                        chatwoot_msg = ChatwootMessage(
                            conversation_id=conv.meta_data.get("chatwoot_conversation_id"),
                            content=final_result["response"],
                            message_type="outgoing",
                            sender_type="agent",
                        )
                        await self.chatwoot_service.send_message(chatwoot_msg)

                return {
                    "status": "success",
                    "response": final_result["response"]
                }

            except Exception as e:
                logger.error("Error en agent_review", error=str(e), exc_info=True)
                raise HTTPException(status_code=500, detail=str(e))

        @app.get("/")

        async def root():
            return {
                "name": self.settings.APP_NAME,
                "version": "1.0.0",
                "status": "running",
                "features": {
                    "llm": "OpenAI GPT-4o-mini",
                    "memory": "PostgreSQL"
                    if self.settings.USE_POSTGRES_FOR_MEMORY
                    else "In-Memory",
                    "agent": "ArcadiumAgent (LangGraph)"
                    if self.settings.USE_LANGGRAPH
                    else "DeyyAgent",
                    "google_calendar": "enabled"
                    if self.settings.GOOGLE_CALENDAR_ENABLED
                    else "disabled",
                },
            }

        @app.get("/api/history/{session_id}")
        async def get_history(session_id: str):
            try:
                history = await self.memory_manager.get_history(session_id)
                return {
                    "session_id": session_id,
                    "messages": [
                        {"type": type(msg).__name__, "content": msg.content}
                        for msg in history
                    ],
                    "count": len(history),
                }
            except Exception as e:
                raise HTTPException(status_code=500, detail=str(e))

        @app.get("/api/calendar/status")
        async def calendar_status():
            try:
                from services.google_calendar_service import get_default_calendar_service
                import os

                gcal = get_default_calendar_service()
                connected = all([
                    os.getenv("GOOGLE_REFRESH_TOKEN"),
                    os.getenv("GOOGLE_CLIENT_ID"),
                    os.getenv("GOOGLE_CLIENT_SECRET"),
                ])
                return {
                    "enabled": self.settings.GOOGLE_CALENDAR_ENABLED,
                    "connected": connected,
                    "calendar_id": gcal.calendar_id,
                }
            except Exception as e:
                return {"enabled": False, "connected": False, "error": str(e)}

        # ── Admin / Client pages ──────────────────────

        @app.get("/admin")
        async def admin_dashboard():
            from fastapi.responses import FileResponse

            path = os.path.join(
                os.path.dirname(__file__), "..", "templates", "admin", "dashboard.html"
            )
            return (
                FileResponse(path)
                if os.path.exists(path)
                else JSONResponse({"error": "Not found"}, status_code=404)
            )

        @app.get("/admin/agent-config")
        async def admin_agent_config():
            from fastapi.responses import FileResponse

            path = os.path.join(
                os.path.dirname(__file__),
                "..",
                "templates",
                "admin",
                "agent_config.html",
            )
            return (
                FileResponse(path)
                if os.path.exists(path)
                else JSONResponse({"error": "Not found"}, status_code=404)
            )

        @app.get("/client")
        async def client_dashboard():
            from fastapi.responses import FileResponse

            path = os.path.join(
                os.path.dirname(__file__), "..", "templates", "client", "dashboard.html"
            )
            return (
                FileResponse(path)
                if os.path.exists(path)
                else JSONResponse({"error": "Not found"}, status_code=404)
            )

        @app.get("/chat")
        async def chat_page():
            from fastapi.responses import FileResponse

            path = os.path.join(
                os.path.dirname(__file__), "..", "templates", "chat.html"
            )
            return (
                FileResponse(path)
                if os.path.exists(path)
                else JSONResponse({"error": "Not found"}, status_code=404)
            )

        # ── WebSocket ─────────────────────────────────

        @app.websocket("/ws/{session_id}")
        async def websocket_endpoint(
            websocket: WebSocket,
            session_id: str,
            x_project_id: Optional[str] = Header(None, alias="X-Project-Id"),
        ):
            await websocket.accept()

            project_id = None
            if x_project_id:
                try:
                    project_id = uuid.UUID(x_project_id)
                except ValueError:
                    logger.warning(
                        "X-Project-Id inválido en WebSocket", value=x_project_id
                    )

            try:
                agent = await self._get_or_create_agent(
                    session_id=session_id, project_id=project_id
                )

                while True:
                    data = await websocket.receive_json()
                    message = data.get("message", "").strip()
                    if not message:
                        continue

                    try:
                        # FIX: normalizar resultado — puede ser AgentResponse o dict
                        raw = await agent.process_message(message)
                        result = _normalize_agent_result(raw)
                        await websocket.send_json(
                            {
                                "type": "response",
                                "content": result["response"],
                                "execution_time": result["execution_time_seconds"],
                            }
                        )
                    except Exception as e:
                        logger.error(
                            "Error procesando mensaje WS",
                            session_id=session_id,
                            error=str(e),
                        )
                        await websocket.send_json({"type": "error", "message": str(e)})

            except WebSocketDisconnect:
                logger.info("WebSocket desconectado", session_id=session_id)
            except Exception as e:
                logger.error(
                    "Error en WebSocket",
                    session_id=session_id,
                    error=str(e),
                    exc_info=True,
                )
            finally:
                try:
                    await websocket.close()
                except Exception:
                    pass

        # ── Webhooks y endpoints principales ─────────

        app.post("/webhook/whatsapp")(self._handle_whatsapp_webhook)
        app.post("/webhook/chatwoot")(self._handle_chatwoot_webhook)
        app.post("/webhook/test")(self._handle_test_webhook)
        app.get("/health")(self._health_check)
        app.get("/metrics")(self._get_metrics)

        if self.settings.DEBUG:
            app.get("/debug/agent/{session_id}")(self._debug_agent)

        try:
            from admin.api import router as admin_router

            app.include_router(admin_router)
            logger.info("Admin API router registrado")
        except ImportError as e:
            logger.warning("Admin API router no disponible", error=str(e))

        return app

    # ============================================
    # Rate limiting helper
    # ============================================

    def _check_rate_limit(self, sender: str) -> bool:
        """
        Verifica si el sender está dentro del límite de mensajes.
        Retorna True si está OK, False si fue excedido.
        """
        now = datetime.now(timezone.utc)
        window_start = now - timedelta(minutes=1)

        if sender in self._rate_limit:
            self._rate_limit[sender] = [
                ts for ts in self._rate_limit[sender] if ts > window_start
            ]
            if len(self._rate_limit[sender]) >= self._rate_limit_max:
                return False
            self._rate_limit[sender].append(now)
        else:
            self._rate_limit[sender] = [now]
        return True

    # ============================================
    # Webhook: WhatsApp
    # ============================================

    async def _handle_whatsapp_webhook(self, request: Request) -> Dict[str, Any]:
        """Webhook principal de WhatsApp."""
        async with self.db.get_session() as session:
            try:
                payload = await request.json()
                logger.info(
                    "Webhook WhatsApp recibido", payload_keys=list(payload.keys())
                )

                message_data = await self._parse_whatsapp_payload(payload)
                if not message_data:
                    return {"status": "ignored", "reason": "Invalid payload"}

                sender = message_data["sender"]

                # Rate limiting
                if not self._check_rate_limit(sender):
                    logger.warning("Rate limit excedido", sender=sender)
                    return {"status": "rate_limited", "reason": "Too many requests"}

                project_id = self._extract_project_id(payload, request)

                conversation = await self._get_or_create_conversation(
                    session,
                    phone_number=sender,
                    platform="whatsapp",
                    project_id=project_id,
                )

                inbound_msg = Message(
                    project_id=conversation.project_id,
                    conversation_id=conversation.id,
                    direction="inbound",
                    message_type=message_data["message_type"],
                    content=message_data["message"],
                    raw_payload=payload,
                    processed=False,
                )
                session.add(inbound_msg)
                await session.flush()

                agent = await self._get_or_create_agent(
                    session_id=sender, project_id=conversation.project_id
                )

                # FIX: process_message solo recibe el mensaje (sin context_vars)
                # FIX: timeout también en WhatsApp
                # FIX: lock por sesión — serializa mensajes concurrentes del mismo número
                async with self._get_session_lock(sender):
                    try:
                        raw = await asyncio.wait_for(
                            agent.process_message(message_data["message"]), timeout=30.0
                        )
                    except asyncio.TimeoutError:
                        logger.error("Timeout procesando mensaje WhatsApp", sender=sender)
                        return {
                            "status": "error",
                            "response": "Disculpe, estoy teniendo dificultades técnicas. Por favor intente de nuevo o llame a la clínica. 📞",
                            "session_id": sender,
                        }

                # FIX: normalizar resultado — AgentResponse o dict
                result = _normalize_agent_result(raw)

                inbound_msg.processed = True
                inbound_msg.agent_response = result["response"]
                inbound_msg.tool_calls = result["tool_calls"]
                inbound_msg.execution_time_ms = result["execution_time_seconds"] * 1000

                if result["status"] in ("ok", "success"):
                    try:
                        await self.whatsapp_service.send_message(
                            WhatsAppMessage(to=sender, text=result["response"])
                        )
                    except WhatsAppError as e:
                        logger.error("Error enviando respuesta WhatsApp", error=str(e))
                        inbound_msg.processing_error = str(e)

                await session.commit()

                logger.info(
                    "Webhook WhatsApp procesado",
                    session_id=agent.session_id,
                    status=result["status"],
                )

                return {
                    "status": "processed",
                    "response": result["response"],
                    "session_id": agent.session_id,
                }

            except Exception as e:
                logger.error(
                    "Error procesando webhook WhatsApp", error=str(e), exc_info=True
                )
                raise HTTPException(status_code=500, detail=f"Error interno: {str(e)}")

    # ============================================
    # Webhook: Chatwoot
    # ============================================

    async def _handle_chatwoot_webhook(self, request: Request) -> Dict[str, Any]:
        """Webhook de Chatwoot."""
        async with self.db.get_session() as session:
            try:
                # Verificar firma si está configurada
                if self.settings.CHATWOOT_WEBHOOK_SECRET:
                    signature = request.headers.get("X-Chatwoot-Signature", "")
                    raw_payload = await request.body()
                    if not ChatwootService.verify_webhook_signature(
                        raw_payload, signature, self.settings.CHATWOOT_WEBHOOK_SECRET
                    ):
                        logger.error("Firma de webhook Chatwoot inválida")
                        raise HTTPException(status_code=401, detail="Invalid signature")
                    payload = json.loads(raw_payload)
                else:
                    payload = await request.json()

                logger.info(
                    "Webhook Chatwoot recibido", payload_keys=list(payload.keys())
                )

                # Parsear y filtrar — parse_webhook_payload ya aplica anti-loop
                webhook_data = ChatwootService.parse_webhook_payload(payload)
                if not webhook_data:
                    # No loguear como error — es normal para mensajes salientes
                    return {"status": "ignored", "reason": "Filtered by parser"}

                # FIX: eliminado check redundante de sender_type aquí
                # parse_webhook_payload ya retorna None para mensajes del agente

                # Normalizar contacto
                try:
                    normalized_contact = ChatwootService.normalize_contact(
                        webhook_data["contact"]
                    )
                except ValueError as e:
                    logger.error("No se pudo normalizar contacto", error=str(e))
                    return {"status": "error", "reason": "Invalid contact"}

                logger.info(
                    "Webhook Chatwoot: datos extraídos",
                    contact=normalized_contact,
                    conversation_id=webhook_data["conversation_id"],
                    content_preview=webhook_data["content"][:50],
                )

                project_id = self._extract_project_id(payload, request)
                if not project_id and self.default_project_id:
                    project_id = self.default_project_id

                conversation = await self._get_or_create_conversation(
                    session,
                    phone_number=normalized_contact,
                    platform="chatwoot",
                    project_id=project_id,
                )

                chatwoot_conv_id = webhook_data["conversation_id"]
                if conversation.meta_data is None:
                    conversation.meta_data = {}
                conversation.meta_data["chatwoot_conversation_id"] = chatwoot_conv_id
                conversation.meta_data["chatwoot_account_id"] = webhook_data.get(
                    "account_id"
                )
                conversation.meta_data["chatwoot_inbox_id"] = webhook_data.get(
                    "inbox_id"
                )

                inbound_msg = Message(
                    project_id=conversation.project_id,
                    conversation_id=conversation.id,
                    direction="inbound",
                    message_type=webhook_data["message_type"],
                    content=webhook_data["content"],
                    raw_payload=payload,
                    processed=False,
                )
                session.add(inbound_msg)
                await session.flush()

                agent = await self._get_or_create_agent(
                    session_id=normalized_contact, project_id=conversation.project_id
                )

                # FIX: timeout en Chatwoot (igual que WhatsApp)
                # FIX: lock por sesión — serializa mensajes concurrentes del mismo número
                async with self._get_session_lock(normalized_contact):
                    try:
                        raw = await asyncio.wait_for(
                            agent.process_message(webhook_data["content"]), timeout=30.0
                        )
                    except asyncio.TimeoutError:
                        logger.error(
                            "Timeout procesando mensaje Chatwoot",
                            contact=normalized_contact,
                        )
                        return {"status": "error", "reason": "Agent timeout"}

                # FIX: normalizar resultado
                result = _normalize_agent_result(raw)

                logger.info(
                    "Resultado del agente",
                    status=result["status"],
                    response_preview=result["response"][:80],
                )

                inbound_msg.processed = True
                inbound_msg.agent_response = result["response"]
                inbound_msg.tool_calls = result["tool_calls"]
                inbound_msg.execution_time_ms = result["execution_time_seconds"] * 1000

                if result["status"] in ("ok", "success"):
                    try:
                        # FIX: sender_type="agent" es clave para el anti-loop
                        chatwoot_msg = ChatwootMessage(
                            conversation_id=chatwoot_conv_id,
                            content=result["response"],
                            message_type="outgoing",
                            sender_type="agent",
                        )
                        await self.chatwoot_service.send_message(chatwoot_msg)
                    except ChatwootError as e:
                        logger.error(
                            "Error enviando respuesta a Chatwoot", error=str(e)
                        )
                        inbound_msg.processing_error = str(e)

                await session.commit()

                logger.info(
                    "Webhook Chatwoot procesado",
                    session_id=agent.session_id,
                    status=result["status"],
                    execution_time=result["execution_time_seconds"],
                )

                return {
                    "status": "processed",
                    "session_id": agent.session_id,
                    "response": result["response"],
                }

            except HTTPException:
                raise
            except Exception as e:
                logger.error(
                    "Error procesando webhook Chatwoot", error=str(e), exc_info=True
                )
                raise HTTPException(status_code=500, detail=f"Error interno: {str(e)}")

    # ============================================
    # Webhook: Test
    # ============================================

    async def _handle_test_webhook(self, request: Request) -> Dict[str, Any]:
        """Endpoint de prueba — no envía a WhatsApp ni Chatwoot."""
        payload = await request.json()
        message = payload.get("message", "")
        raw_session_id = payload.get("session_id", "test_session")

        if (
            raw_session_id != "test_session"
            and raw_session_id.replace("+", "").replace(" ", "").isdigit()
        ):
            try:
                session_id = normalize_phone(raw_session_id)
            except ValueError:
                session_id = raw_session_id
        else:
            session_id = raw_session_id

        project_id = self._extract_project_id(payload, request)
        if not project_id and self.default_project_id:
            project_id = self.default_project_id

        agent = await self._get_or_create_agent(
            session_id=session_id, project_id=project_id
        )

        # FIX: normalizar resultado
        raw = await agent.process_message(message)
        return _normalize_agent_result(raw)

    # ============================================
    # Parseo de payloads
    # ============================================

    async def _parse_whatsapp_payload(
        self, payload: Dict[str, Any]
    ) -> Optional[Dict[str, Any]]:
        """Parsea payload de WhatsApp (Evolution API o formato Chatwoot anidado)."""
        # Formato 1: Evolution API (plano)
        if all(k in payload for k in ["sender", "message", "message_type"]):
            raw_sender = payload["sender"]
            try:
                normalized_sender = normalize_phone(raw_sender)
            except ValueError:
                normalized_sender = raw_sender
            return {
                "sender": normalized_sender,
                "message": payload["message"],
                "message_type": payload["message_type"],
            }

        # Formato 2: Chatwoot (anidado)
        if "body" in payload and "conversation" in payload["body"]:
            conv = payload["body"]["conversation"]
            if "messages" in conv and conv["messages"]:
                msg = conv["messages"][0]
                phone = msg.get("sender", {}).get("phone_number")
                content = msg.get("content")
                if phone and content:
                    try:
                        normalized_phone = normalize_phone(phone)
                    except ValueError:
                        normalized_phone = phone
                    return {
                        "sender": normalized_phone,
                        "message": content,
                        "message_type": "text",
                    }

        logger.warning(
            "Formato de payload no reconocido", payload_keys=list(payload.keys())
        )
        return None

    # ============================================
    # Conversaciones
    # ============================================

    async def _get_or_create_conversation(
        self,
        session: AsyncSession,
        phone_number: str,
        platform: str = "whatsapp",
        project_id: Optional[uuid.UUID] = None,
    ) -> Conversation:
        stmt = select(Conversation).where(
            and_(
                Conversation.phone_number == phone_number,
                Conversation.platform == platform,
                Conversation.status == "active",
            )
        )
        if project_id:
            stmt = stmt.where(Conversation.project_id == project_id)

        result = await session.execute(stmt)
        conversation = result.scalar_one_or_none()

        if not conversation:
            if project_id is None:
                res_default = await session.execute(
                    select(Project).where(Project.slug == "default")
                )
                default_project = res_default.scalar_one_or_none()
                if default_project:
                    project_id = default_project.id

            conversation = Conversation(
                phone_number=phone_number,
                platform=platform,
                status="active",
                project_id=project_id,
                agent_enabled=True,
            )
            session.add(conversation)
            await session.flush()
            logger.info(
                "Nueva conversación creada",
                phone=phone_number,
                project_id=str(project_id) if project_id else None,
            )

        return conversation

    # ============================================
    # Agentes
    # ============================================

    def _get_session_lock(self, session_id: str) -> asyncio.Lock:
        """
        Retorna (o crea) el Lock asyncio para una sesión dada.

        Serializa requests concurrentes del mismo número. Sin esto, dos mensajes
        del mismo usuario que llegan con <2s de diferencia se procesan en paralelo,
        leen el mismo estado pre-booking, y producen respuestas inconsistentes.
        """
        if session_id not in self._session_locks:
            self._session_locks[session_id] = asyncio.Lock()
        return self._session_locks[session_id]

    async def _get_or_create_agent(
        self,
        session_id: str,
        project_id: Optional[uuid.UUID] = None,
        project_config: Optional[Any] = None,
    ) -> Any:
        cache_key = f"{project_id}:{session_id}" if project_id else session_id

        if cache_key in self._agents:
            # LRU: mover al final (más reciente)
            self._agents.move_to_end(cache_key)
            logger.info(
                "Reutilizando agente (CACHE HIT)",
                cache_key=cache_key,
                cache_size=len(self._agents),
            )
            return self._agents[cache_key]

        logger.info(
            "Creando nuevo agente (CACHE MISS)",
            cache_key=cache_key,
            cache_size=len(self._agents),
        )

        try:
            if project_config is None and project_id:
                async with self.db.get_session() as db_session:
                    from sqlalchemy.orm import selectinload

                    stmt = (
                        select(ProjectAgentConfig)
                        .options(selectinload(ProjectAgentConfig.project))
                        .where(ProjectAgentConfig.project_id == project_id)
                    )
                    result = await db_session.execute(stmt)
                    project_config = result.scalar_one_or_none()
                    if not project_config:
                        project_config = await self._get_or_create_default_config(
                            project_id, db_session
                        )

            if self.settings.USE_LANGGRAPH:
                agent = await self._create_langgraph_agent(
                    session_id=session_id,
                    project_id=project_id,
                )
            elif self.settings.ENABLE_STATE_MACHINE:
                # RouterAgent was removed/merged into DeyyAgent in v2.1
                # Fallback to DeyyAgent as it now handles the State Machine logic
                from agents.deyy_agent import DeyyAgent

                agent = DeyyAgent(
                    session_id=session_id,
                    store=self.store,
                    project_id=project_id,
                    project_config=project_config,
                    whatsapp_service=self.whatsapp_service,
                    system_prompt=self.settings.AGENT_SYSTEM_PROMPT,
                    llm_model=self.settings.OPENAI_MODEL,
                    llm_temperature=self.settings.OPENAI_TEMPERATURE,
                    max_iterations=self.settings.AGENT_MAX_ITERATIONS,
                    verbose=self.settings.AGENT_VERBOSE,
                )
            else:
                from agents.deyy_agent import DeyyAgent


                agent = DeyyAgent(
                    session_id=session_id,
                    store=self.store,
                    project_id=project_id,
                    project_config=project_config,
                    whatsapp_service=self.whatsapp_service,
                    system_prompt=self.settings.AGENT_SYSTEM_PROMPT,
                    llm_model=self.settings.OPENAI_MODEL,
                    llm_temperature=self.settings.OPENAI_TEMPERATURE,
                    max_iterations=self.settings.AGENT_MAX_ITERATIONS,
                    verbose=self.settings.AGENT_VERBOSE,
                )

            # FIX: LRU — si el cache está lleno, eliminar el más antiguo
            if len(self._agents) >= self._AGENT_CACHE_MAX:
                oldest_key, _ = self._agents.popitem(last=False)
                logger.info("Cache LRU: agente eliminado", evicted_key=oldest_key)

            self._agents[cache_key] = agent
            return agent

        except Exception as e:
            logger.error(
                "Error creando agente", cache_key=cache_key, error=str(e), exc_info=True
            )
            raise

    async def _get_or_create_default_config(
        self, project_id: uuid.UUID, session: AsyncSession
    ) -> ProjectAgentConfig:
        result = await session.execute(select(Project).where(Project.id == project_id))
        project = result.scalar_one_or_none()
        if not project:
            raise ValueError(f"Proyecto {project_id} no encontrado")

        config = ProjectAgentConfig(
            project_id=project_id,
            agent_name="DeyyAgent",
            system_prompt=f"Eres un asistente AI útil para el proyecto {project.name}.",
            max_iterations=10,
            temperature=0.7,
            enabled_tools=[
                "agendar_cita",
                "consultar_disponibilidad",
                "obtener_citas_cliente",
                "cancelar_cita",
            ],
            calendar_enabled=False,
            global_agent_enabled=True,
        )
        session.add(config)
        await session.flush()
        await session.commit()
        return config

    def _extract_project_id(
        self, payload: Dict[str, Any], request: Request
    ) -> Optional[uuid.UUID]:
        """Extrae project_id del webhook (header > payload > None)."""
        header = request.headers.get("X-Project-Id")
        if header:
            try:
                return uuid.UUID(header)
            except ValueError:
                logger.warning("X-Project-Id header inválido", value=header)

        if "project_id" in payload:
            try:
                return uuid.UUID(payload["project_id"])
            except (ValueError, TypeError):
                logger.warning(
                    "project_id en payload inválido", value=payload.get("project_id")
                )

        return None

    # ============================================
    # Endpoints auxiliares
    # ============================================

    async def _health_check(self) -> Dict[str, Any]:
        return {
            "status": "healthy",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "version": "1.0.0",
            "active_agents": len(self._agents),
        }

    async def _get_metrics(self) -> Dict[str, Any]:
        return {
            "active_sessions": len(self._agents),
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

    async def _debug_agent(self, session_id: str) -> Dict[str, Any]:
        if not self.settings.DEBUG:
            raise HTTPException(status_code=404, detail="Not found")
        agent = self._agents.get(session_id)
        if not agent:
            raise HTTPException(status_code=404, detail="Agent not found")
        history = await self.memory_manager.get_history(session_id)
        return {
            "session_id": session_id,
            "initialized": getattr(agent, "_initialized", None),
            "message_count": len(history),
            "history": [
                {"type": type(msg).__name__, "content": msg.content[:100]}
                for msg in history[-10:]
            ],
        }

    # ============================================
    # Shutdown
    # ============================================

    async def shutdown(self):
        logger.info("Cerrando ArcadiumAPI")
        for agent in self._agents.values():
            if hasattr(agent, "memory_manager"):
                try:
                    await agent.memory_manager.cleanup_expired_sessions()
                except Exception:
                    pass
        if self.whatsapp_service:
            try:
                await self.whatsapp_service.disconnect()
            except Exception:
                pass
        if self.db:
            await self.db.engine.dispose()
        logger.info("ArcadiumAPI cerrado")

    def start(self):
        import uvicorn

        app = self.create_app()
        config = uvicorn.Config(
            app,
            host=self.settings.HOST,
            port=self.settings.PORT,
            workers=1,
            reload=False,
            log_level="info",
        )
        server = uvicorn.Server(config)
        try:
            print(
                f"🚀 Servidor iniciado en http://{self.settings.HOST}:{self.settings.PORT}"
            )
            server.run()
        except KeyboardInterrupt:
            print("\n🛑 Servidor detenido")


# ============================================
# Entry points
# ============================================


def create_app() -> FastAPI:
    api = ArcadiumAPI()
    return api.create_app()


async def main():
    settings = get_settings()
    api = ArcadiumAPI(settings)
    app = api.create_app()
    import uvicorn

    uvicorn.run(app, host=settings.HOST, port=settings.PORT, workers=settings.WORKERS)


if __name__ == "__main__":
    asyncio.run(main())


# Alias para compatibilidad
ArcadiumAutomation = ArcadiumAPI
