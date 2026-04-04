#!/usr/bin/env python3
"""
Orquestador principal de Arcadium
Sin dependencia en n8n - comunicación directa con WhatsApp API
"""

import asyncio
from typing import Dict, Any, Optional
from datetime import datetime, timezone
import os
import uuid
import structlog
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, HTTPException, Depends, WebSocket, WebSocketDisconnect, Header
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse

from core.config import get_settings
from memory.memory_manager import MemoryManager
from services.whatsapp_service import WhatsAppService, WhatsAppMessage, WhatsAppError
from db.models import Conversation, Message, Base, ProjectAgentConfig, Project
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy import select, and_

from utils.logger import setup_logger
from utils.phone_utils import normalize_phone

logger = structlog.get_logger("orchestrator")


# ============================================
# Database Setup
# ============================================

class Database:
    """Gestor de base de datos"""

    def __init__(self, url: str):
        # Asegurar que usa driver async (asyncpg)
        if '+asyncpg' not in url and '+psycopg' not in url:
            url = url.replace('postgresql://', 'postgresql+asyncpg://', 1)
        self.engine = create_async_engine(
            url,
            echo=False,
            pool_size=10,
            max_overflow=20
        )
        self.async_session_maker = sessionmaker(
            self.engine,
            class_=AsyncSession,
            expire_on_commit=False
        )

    async def init(self):
        """Inicializa tablas"""
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        logger.info("Base de datos inicializada")

    def get_session(self) -> AsyncSession:
        """Obtiene nueva sesión"""
        return self.async_session_maker()


# ============================================
# FastAPI App
# ============================================

class ArcadiumAPI:
    """
    API principal de Arcadium
    Maneja webhooks de WhatsApp y endpoints internos
    """

    def __init__(self, settings=None):
        self.settings = settings or get_settings()
        self.db: Optional[Database] = None
        self.memory_manager: Optional[MemoryManager] = None
        self.whatsapp_service: Optional[WhatsAppService] = None
        self._agents: Dict[str, Any] = {}
        self.default_project_id: Optional[uuid.UUID] = None

        logger.info("ArcadiumAPI creada")

    async def initialize(self):
        """Inicializa todos los componentes"""
        logger.info("Inicializando ArcadiumAPI")

        # Configurar logging
        setup_logger(self.settings.LOG_LEVEL)

        # Inicializar DB
        self.db = Database(self.settings.DATABASE_URL)
        await self.db.init()

        # Inicializar session maker global para db.get_async_session()
        from db import init_session_maker
        init_session_maker(self.db.engine)

        # Run database migrations automáticamente
        await self._run_migrations()

        # Cargar proyecto por defecto (para test webhook y modos sin project_id explícito)
        await self._load_default_project()

        # Inicializar Memory Manager
        self.memory_manager = MemoryManager(self.settings)
        await self.memory_manager.initialize()

        # Inicializar Store (wrapper sobre MemoryManager)
        from core.store import ArcadiumStore
        self.store = ArcadiumStore(self.memory_manager)

        # Inicializar WhatsApp Service
        self.whatsapp_service = WhatsAppService(self.settings)

        # Cargar métricas si está habilitado
        if self.settings.ENABLE_METRICS:
            self._setup_metrics()

        logger.info("ArcadiumAPI inicializada")

    def _setup_metrics(self):
        """Configura métricas Prometheus"""
        try:
            from prometheus_client import start_http_server
            start_http_server(self.settings.METRICS_PORT)
            logger.info("Métricas Prometheus iniciadas", port=self.settings.METRICS_PORT)
        except ImportError:
            logger.warning("prometheus_client no instalado, métricas deshabilitadas")

    async def _load_default_project(self) -> None:
        """Carga el proyecto por defecto desde la base de datos"""
        logger.info("Iniciando carga de proyecto por defecto")
        try:
            from db import get_async_session
            from db.models import Project

            async with get_async_session() as session:
                stmt = select(Project).where(Project.is_active == True).limit(1)
                result = await session.execute(stmt)
                project = result.scalar_one_or_none()

                if project:
                    self.default_project_id = project.id
                    logger.info("Proyecto por defecto cargado", project_id=str(project.id), project_name=project.name)
                else:
                    logger.warning("No hay proyectos activos en DB, test webhook no funcionará con project_id")
                    self.default_project_id = None
        except Exception as e:
            logger.error("Error cargando proyecto por defecto", error=str(e), exc_info=True)
            self.default_project_id = None
        logger.info("Carga de proyecto por defecto completada", default_project_id=str(self.default_project_id))

    async def _run_migrations(self):
        """Ejecuta migraciones de base de datos automáticamente"""
        logger.info("Running database migrations...")
        try:
            import asyncio
            from db.migrate import run_migrations_sync

            # Ejecutar migración en thread separado (psycopg2 es blocking)
            await asyncio.to_thread(run_migrations_sync)
            logger.info("Migrations completed successfully")
        except ImportError as e:
            logger.warning("Migration script not available", error=str(e))
        except Exception as e:
            logger.error("Migration failed", error=str(e))
            raise

    def create_app(self) -> FastAPI:
        """
        Crea y configura aplicación FastAPI

        Returns:
            FastAPI app configurada
        """
        @asynccontextmanager
        async def lifespan(app: FastAPI):
            """Maneja ciclo de vida de la aplicación"""
            try:
                await self.initialize()
                # Exponer instancia de API en app.state para acceso desde routers
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
            debug=self.settings.DEBUG
        )

        # CORS
        app.add_middleware(
            CORSMiddleware,
            allow_origins=self.settings.CORS_ORIGINS,
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
        )

        # Logging middleware
        @app.middleware("http")
        async def log_requests(request: Request, call_next):
            """Middleware para logging de todas las peticiones"""
            from datetime import datetime
            start_time = datetime.utcnow()

            # Filtrar headers sensibles
            headers = dict(request.headers)
            if 'x-api-key' in headers:
                headers['x-api-key'] = '***REDACTED***'

            # Log de entrada
            logger.info(
                "Request started",
                method=request.method,
                url=str(request.url),
                client=request.client.host if request.client else None,
                headers=headers
            )

            # Procesar request
            try:
                response = await call_next(request)
                elapsed = (datetime.utcnow() - start_time).total_seconds() * 1000

                # Log de salida
                logger.info(
                    "Request completed",
                    method=request.method,
                    url=str(request.url),
                    status_code=response.status_code,
                    elapsed_ms=round(elapsed, 2)
                )
                return response
            except HTTPException as e:
                elapsed = (datetime.utcnow() - start_time).total_seconds() * 1000
                logger.warning(
                    "Request HTTP error",
                    method=request.method,
                    url=str(request.url),
                    status_code=e.status_code,
                    detail=str(e.detail)[:200],
                    elapsed_ms=round(elapsed, 2)
                )
                raise
            except Exception as e:
                elapsed = (datetime.utcnow() - start_time).total_seconds() * 1000
                logger.error(
                    "Request error",
                    method=request.method,
                    url=str(request.url),
                    error=str(e)[:500],
                    elapsed_ms=round(elapsed, 2),
                    exc_info=True
                )
                raise

        # Serve static files
        from fastapi.staticfiles import StaticFiles
        import os
        static_dir = os.path.join(os.path.dirname(__file__), "..", "static")
        if os.path.exists(static_dir):
            app.mount("/static", StaticFiles(directory=static_dir), name="static")
            logger.info("Static files served from", directory=static_dir)
        else:
            logger.warning("Static directory not found", path=static_dir)

        # ============================================
        # OAUTH2 ENDPOINTS (Google Calendar) - Admin only
        # ============================================
        from fastapi.responses import RedirectResponse

        @app.get("/auth/google")
        async def auth_google():
            """
            Inicia flujo OAuth2 con Google Calendar.
            Para configurar la integración (uso admin).
            Redirige a Google; después del callback, guarda token y redirige a la raíz.
            """
            try:
                from services.google_calendar_service import GoogleCalendarService

                if not self.settings.GOOGLE_REDIRECT_URI:
                    return JSONResponse({
                        "error": "GOOGLE_REDIRECT_URI no configurado en .env"
                    }, status_code=500)

                gcal = GoogleCalendarService(
                    calendar_id=self.settings.GOOGLE_CALENDAR_DEFAULT_ID,
                    credentials_path=self.settings.GOOGLE_CALENDAR_CREDENTIALS_PATH,
                    timezone=self.settings.GOOGLE_CALENDAR_TIMEZONE,
                    redirect_uri=self.settings.GOOGLE_REDIRECT_URI
                )

                auth_url = gcal.get_authorization_url()

                logger.info("Redirigiendo a Google OAuth (configuración admin)", url=auth_url)
                return RedirectResponse(url=auth_url)

            except Exception as e:
                logger.error("Error generando auth URL", error=str(e))
                raise HTTPException(status_code=500, detail=str(e))

        @app.get("/oauth2callback")
        async def oauth2callback(code: str):
            """
            Callback de OAuth2 desde Google.
            Intercambia el código por tokens y guarda token.json.
            Redirige a la página principal.
            """
            try:
                from services.google_calendar_service import GoogleCalendarService

                gcal = GoogleCalendarService(
                    calendar_id=self.settings.GOOGLE_CALENDAR_DEFAULT_ID,
                    credentials_path=self.settings.GOOGLE_CALENDAR_CREDENTIALS_PATH,
                    timezone=self.settings.GOOGLE_CALENDAR_TIMEZONE,
                    redirect_uri=self.settings.GOOGLE_REDIRECT_URI
                )

                creds = gcal.exchange_code_for_tokens(code)

                logger.info(
                    "OAuth callback exitoso - token guardado",
                    access_token=creds.token[:20] + "...",
                    refresh_token_exists=bool(creds.refresh_token)
                )

                # Redirigir a la página principal
                return RedirectResponse(url="/")

            except Exception as e:
                logger.error("Error en oauth2callback", error=str(e), exc_info=True)
                html = f"""
                <!DOCTYPE html>
                <html>
                <head><title>❌ Error</title></head>
                <body>
                    <h1>Error de Autorización</h1>
                    <p>{str(e)}</p>
                    <p><a href="/">Volver al inicio</a></p>
                </body>
                </html>
                """
                return HTMLResponse(content=html, status_code=500)

        # Endpoint raíz - Información de la API
        @app.get("/")
        async def root():
            """Información básica de la API"""
            return {
                "name": self.settings.APP_NAME,
                "version": "1.0.0",
                "status": "running",
                "description": "Arcadium Automation - WhatsApp Automation System",
                "endpoints": {
                    "health": "/health",
                    "metrics": "/metrics",
                    "webhook": "/webhook/whatsapp",
                    "webhook_test": "/webhook/test",
                    "chat": "/chat (interfaz web)",
                    "admin": "/admin/* (panel de gestión)",
                    "api_history": "/api/history/{session_id}",
                    "websocket": "/ws/{session_id}",
                    "auth_google": "/auth/google (iniciar OAuth)",
                    "oauth2callback": "/oauth2callback (callback automático)"
                },
                "features": {
                    "llm": "OpenAI GPT-4o-mini",
                    "memory": "PostgreSQL" if self.settings.USE_POSTGRES_FOR_MEMORY else "In-Memory",
                    "database": "PostgreSQL",
                    "agent": "DeyyAgent with LangChain",
                    "google_calendar": "enabled" if self.settings.GOOGLE_CALENDAR_ENABLED else "disabled"
                }
            }

        # Admin pages routing
        @app.get("/admin")
        async def admin_dashboard():
            """Dashboard principal del admin"""
            from fastapi.responses import FileResponse
            import os
            html_path = os.path.join(os.path.dirname(__file__), "..", "templates", "admin", "dashboard.html")
            if os.path.exists(html_path):
                return FileResponse(html_path)
            return JSONResponse({"error": "Admin dashboard not found"}, status_code=404)

        @app.get("/admin/agent-config")
        async def admin_agent_config():
            """Página de configuración del agente"""
            from fastapi.responses import FileResponse
            import os
            html_path = os.path.join(os.path.dirname(__file__), "..", "templates", "admin", "agent_config.html")
            if os.path.exists(html_path):
                return FileResponse(html_path)
            return JSONResponse({"error": "Agent config page not found"}, status_code=404)

        @app.get("/client")
        async def client_dashboard():
            """Dashboard del cliente"""
            from fastapi.responses import FileResponse
            import os
            html_path = os.path.join(os.path.dirname(__file__), "..", "templates", "client", "dashboard.html")
            if os.path.exists(html_path):
                return FileResponse(html_path)
            return JSONResponse({"error": "Client dashboard not found"}, status_code=404)

        # ============================================
        # CHAT INTERFACE & WEBSOCKET
        # ============================================

        @app.get("/chat")
        async def chat_page():
            """Servir la interfaz de chat"""
            try:
                from fastapi.responses import FileResponse
                import os
                html_path = os.path.join(os.path.dirname(__file__), "..", "templates", "chat.html")
                if os.path.exists(html_path):
                    return FileResponse(html_path)
                else:
                    return JSONResponse(
                        {"error": "Chat interface not found. Please create templates/chat.html"},
                        status_code=404
                    )
            except Exception as e:
                logger.error("Error sirviendo chat page", error=str(e))
                raise HTTPException(status_code=500, detail="Internal server error")

        @app.get("/api/history/{session_id}")
        async def get_history(session_id: str):
            """Obtiene historial de conversación para una sesión"""
            try:
                history = await self.memory_manager.get_history(session_id)
                messages = []
                for msg in history:
                    messages.append({
                        "type": type(msg).__name__,
                        "content": msg.content,
                        "timestamp": getattr(msg, 'timestamp', None)
                    })
                return {
                    "session_id": session_id,
                    "messages": messages,
                    "count": len(messages)
                }
            except Exception as e:
                logger.error("Error obteniendo historial", session_id=session_id, error=str(e))
                raise HTTPException(status_code=500, detail=str(e))

        @app.get("/api/calendar/status")
        async def calendar_status():
            """Obtiene el estado de la integración de Google Calendar"""
            try:
                from services.google_calendar_service import get_default_calendar_service

                gcal = get_default_calendar_service()
                token_exists = os.path.exists(
                    os.path.join(os.path.dirname(gcal.credentials_path), 'token.json')
                )

                return {
                    "enabled": self.settings.GOOGLE_CALENDAR_ENABLED,
                    "connected": token_exists,
                    "calendar_id": gcal.calendar_id,
                    "timezone": gcal.timezone
                }
            except Exception as e:
                logger.error("Error obteniendo estado de calendar", error=str(e))
                return {
                    "enabled": False,
                    "connected": False,
                    "error": str(e)
                }

        @app.websocket("/ws/{session_id}")
        async def websocket_endpoint(
            websocket: WebSocket,
            session_id: str,
            x_project_id: Optional[str] = Header(None, alias="X-Project-Id")
        ):
            """WebSocket para chat en tiempo real"""
            logger.info("WebSocket connection attempt", session_id=session_id, x_project_id=x_project_id)
            try:
                await websocket.accept()
                logger.info(
                    "WebSocket connected and accepted",
                    session_id=session_id,
                    x_project_id=x_project_id
                )
            except Exception as e:
                logger.error("WebSocket accept failed", session_id=session_id, error=str(e), exc_info=True)
                raise

            # Parsear project_id si se provee
            project_id = None
            if x_project_id:
                try:
                    project_id = uuid.UUID(x_project_id)
                except ValueError:
                    logger.warning("X-Project-Id inválido en WebSocket", value=x_project_id)

            try:
                # Inicializar agente para esta sesión con project_id
                agent = await self._get_or_create_agent(
                    session_id=session_id,
                    project_id=project_id
                )

                while True:
                    # Recibir mensaje del cliente
                    data = await websocket.receive_json()
                    message = data.get("message", "").strip()

                    if not message:
                        continue

                    logger.info("Mensaje recibido via WS", session_id=session_id, message=message[:50])

                    # Procesar con agente
                    try:
                        result = await agent.process_message(message)

                        # Log detallado de herramientas usadas (solo para depuración)
                        if result.get("tool_calls"):
                            logger.info(
                                "Herramientas utilizadas",
                                session_id=session_id,
                                tools=result.get("tool_calls", [])
                            )

                        # Enviar respuesta al cliente (sin tool_calls, solo la respuesta natural)
                        await websocket.send_json({
                            "type": "response",
                            "content": result.get("response", ""),
                            "execution_time": result.get("execution_time_seconds", 0)
                        })

                    except Exception as e:
                        logger.error("Error procesando mensaje WS", session_id=session_id, error=str(e))
                        await websocket.send_json({
                            "type": "error",
                            "message": f"Error: {str(e)}"
                        })

            except WebSocketDisconnect:
                logger.info("WebSocket desconectado", session_id=session_id)
            except Exception as e:
                import traceback
                logger.error("Error en WebSocket", session_id=session_id, error=str(e))
                print("\n=== TRACEBACK DEBUG ===")
                traceback.print_exc()
                print("=== END TRACEBACK ===\n")
            finally:
                # Cerrar conexión
                try:
                    await websocket.close()
                except:
                    pass

        # Endpoints originales
        app.post("/webhook/whatsapp")(self._handle_whatsapp_webhook)
        app.post("/webhook/test")(self._handle_test_webhook)
        app.get("/health")(self._health_check)
        app.get("/metrics")(self._get_metrics)

        if self.settings.DEBUG:
            app.get("/debug/agent/{session_id}")(self._debug_agent)

        # Incluir admin API router
        try:
            from admin.api import router as admin_router
            app.include_router(admin_router)
            logger.info("Admin API router registrado")
        except ImportError as e:
            logger.warning("Admin API router no disponible", error=str(e))

        logger.info("FastAPI app creada con endpoints")
        return app

    async def _handle_whatsapp_webhook(
        self,
        request: Request
    ) -> Dict[str, Any]:
        """
        Webhook principal de WhatsApp
        Recibe mensajes y los procesa a través del agente
        """
        async with self.db.get_session() as session:
            try:
                # Validar webhook (opcional)
                if self.settings.WEBHOOK_SECRET:
                    _ = request.headers.get("X-Hub-Signature-256", "")
                    # TODO: Implementar verificación de firma

                # Parsear payload
                payload = await request.json()
                logger.info("Webhook recibido", payload_keys=list(payload.keys()))

                # Extraer datos del mensaje
                message_data = await self._parse_whatsapp_payload(payload)

                if not message_data:
                    logger.warning("Payload no válido o ignorado")
                    return {"status": "ignored", "reason": "Invalid payload"}

                # LOG DIAGNÓSTICO: session_id que vamos a usar
                logger.info(
                    "Webhook: datos extraídos",
                    sender=message_data["sender"],
                    message_preview=message_data["message"][:50],
                    session_id_derived=message_data["sender"]  # session_id = sender
                )

                # Extraer project_id del payload, header, o usar default
                project_id = self._extract_project_id(payload, request)

                # Crear o recuperar conversación con project_id
                conversation = await self._get_or_create_conversation(
                    session,
                    phone_number=message_data["sender"],
                    platform="whatsapp",
                    project_id=project_id
                )

                # Guardar mensaje entrante (con project_id de la conversación)
                inbound_msg = Message(
                    project_id=conversation.project_id,
                    conversation_id=conversation.id,
                    direction="inbound",
                    message_type=message_data["message_type"],
                    content=message_data["message"],
                    raw_payload=payload,
                    processed=False
                )
                session.add(inbound_msg)
                await session.flush()

                # Procesar con agente
                logger.info(
                    "Obteniendo/creando agente",
                    requested_session_id=message_data["sender"],
                    project_id=str(conversation.project_id) if conversation.project_id else None,
                    cache_size=len(self._agents),
                    cache_keys=list(self._agents.keys())
                )
                agent = await self._get_or_create_agent(
                    session_id=message_data["sender"],
                    project_id=conversation.project_id
                )
                logger.info(
                    "Agente obtenido",
                    session_id=agent.session_id,
                    cache_hit=message_data["sender"] in self._agents,
                    cache_size=len(self._agents)
                )
                # Extraer variables de contexto (fechas calculadas) si están presentes
                context_vars = payload.get("context_vars", {})

                # Log de contexto si está presente
                if context_vars:
                    logger.debug("Contexto recibido", context_keys=list(context_vars.keys()))

                result = await agent.process_message(
                    message_data["message"],
                    context_vars=context_vars
                )

                # Actualizar mensaje con resultado
                inbound_msg.processed = True
                inbound_msg.agent_response = result["response"]
                inbound_msg.tool_calls = result.get("tool_calls", [])
                inbound_msg.execution_time_ms = result.get("execution_time_seconds", 0) * 1000

                # Enviar respuesta a WhatsApp
                if result["status"] == "success":
                    try:
                        whatsapp_msg = WhatsAppMessage(
                            to=message_data["sender"],
                            text=result["response"]
                        )
                        send_result = await self.whatsapp_service.send_message(whatsapp_msg)
                        inbound_msg.raw_payload["whatsapp_response"] = send_result
                    except WhatsAppError as e:
                        logger.error("Error enviando respuesta WhatsApp", error=str(e))
                        inbound_msg.processing_error = f"WhatsApp send failed: {str(e)}"

                await session.commit()

                logger.info(
                    "Webhook procesado",
                    session_id=agent.session_id,
                    status=result["status"],
                    execution_time=result.get("execution_time_seconds", 0)
                )

                return {
                    "status": "processed",
                    "response": result["response"],
                    "session_id": agent.session_id
                }

            except Exception as e:
                logger.error("Error procesando webhook", error=str(e), exc_info=True)
                raise HTTPException(status_code=500, detail=f"Error interno: {str(e)}")

    async def _handle_test_webhook(self, request: Request) -> Dict[str, Any]:
        """
        Endpoint de prueba para testear integración
        No envía a WhatsApp, solo procesa
        """
        payload = await request.json()
        message = payload.get("message", "")

        raw_session_id = payload.get("session_id", "test_session")
        logger.info("Test webhook: raw_session_id recibido", raw_session_id=raw_session_id)

        # Normalizar si es un número de teléfono
        if raw_session_id != "test_session" and raw_session_id.replace("+", "").replace(" ", "").isdigit():
            try:
                session_id = normalize_phone(raw_session_id)
                logger.info("Test webhook: session_id normalizado", raw=raw_session_id, normalized=session_id)
            except ValueError:
                logger.warning("Test webhook: normalización falló", raw=raw_session_id)
                session_id = raw_session_id
        else:
            session_id = raw_session_id
            logger.info("Test webhook: sin normalización", raw=raw_session_id, final=session_id)

        # Extraer project_id (opcional)
        project_id = self._extract_project_id(payload, request)
        # Si no se provee, usar default_project_id
        if not project_id and self.default_project_id:
            project_id = self.default_project_id
            logger.debug("Test webhook: usando project_id por defecto", project_id=str(project_id))

        agent = await self._get_or_create_agent(
            session_id=session_id,
            project_id=project_id
        )
        result = await agent.process_message(message)

        return result

    async def _parse_whatsapp_payload(self, payload: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """
        Parsea payload de WhatsApp (múltiples formatos soportados)

        Formatos:
        1. Evolution API: {sender, message, message_type}
        2. Chatwoot: {body: {conversation: {messages: [{sender: {phone_number}, content}]}}}

        Returns:
            Dict con sender, message, message_type o None si no es mensaje válido
        """
        # Formato 1: Evolution API (plano)
        if all(k in payload for k in ["sender", "message", "message_type"]):
            raw_sender = payload["sender"]
            try:
                normalized_sender = normalize_phone(raw_sender)
            except ValueError:
                normalized_sender = raw_sender  # Fallback: usar el original
            logger.info("Formato Evolution API detectado", raw_sender=raw_sender, normalized_sender=normalized_sender)
            return {
                "sender": normalized_sender,
                "message": payload["message"],
                "message_type": payload["message_type"]
            }

        # Formato 2: Chatwoot (anidado)
        if "body" in payload and "conversation" in payload["body"]:
            conv = payload["body"]["conversation"]
            if "messages" in conv and len(conv["messages"]) > 0:
                msg = conv["messages"][0]
                sender = msg.get("sender", {})
                phone = sender.get("phone_number")
                content = msg.get("content")
                if phone and content:
                    try:
                        normalized_phone = normalize_phone(phone)
                    except ValueError:
                        normalized_phone = phone
                    logger.info("Formato Chatwoot detectado", raw_phone=phone, normalized_phone=normalized_phone)
                    return {
                        "sender": normalized_phone,
                        "message": content,
                        "message_type": "text"
                    }

        logger.warning("Formato de payload no reconocido", payload_keys=list(payload.keys()))
        return None

    async def _get_or_create_conversation(
        self,
        session: AsyncSession,
        phone_number: str,
        platform: str = "whatsapp",
        project_id: Optional[uuid.UUID] = None
    ) -> Conversation:
        """
        Obtiene o crea conversación por número de teléfono y proyecto

        Args:
            session: Sesión de DB
            phone_number: Número de teléfono
            platform: Plataforma de origen
            project_id: ID del proyecto (opcional, si None se busca default)

        Returns:
            Conversation
        """
        stmt = select(Conversation).where(
            and_(
                Conversation.phone_number == phone_number,
                Conversation.platform == platform,
                Conversation.status == "active"
            )
        )

        # Si hay project_id, filtrar también
        if project_id:
            stmt = stmt.where(Conversation.project_id == project_id)

        result = await session.execute(stmt)
        conversation = result.scalar_one_or_none()

        if not conversation:
            # Si no se provee project_id, buscar proyecto 'default'
            if project_id is None:
                stmt_default = select(Project).where(Project.slug == "default")
                res_default = await session.execute(stmt_default)
                default_project = res_default.scalar_one_or_none()
                if default_project:
                    project_id = default_project.id
                    logger.debug(
                        "Usando proyecto default",
                        project_id=str(project_id),
                        phone=phone_number
                    )
                else:
                    logger.warning(
                        "No se encontró proyecto 'default', creando conversación sin proyecto"
                    )

            conversation = Conversation(
                phone_number=phone_number,
                platform=platform,
                status="active",
                project_id=project_id,
                agent_enabled=True
            )
            session.add(conversation)
            await session.flush()
            logger.info(
                "Nueva conversación creada",
                phone=phone_number,
                project_id=str(project_id) if project_id else None
            )

        return conversation

    async def _get_or_create_agent(
        self,
        session_id: str,
        project_id: Optional[uuid.UUID] = None,
        project_config: Optional[Any] = None
    ) -> Any:
        """
        Obtiene o crea agente para una sesión

        Args:
            session_id: ID de sesión (teléfono)
            project_id: ID del proyecto (opcional, si no se provee se usa default)
            project_config: Configuración del agente para el proyecto (opcional)

        Returns:
            DeyyAgent configurado
        """
        cache_key = f"{project_id}:{session_id}" if project_id else session_id

        if cache_key not in self._agents:
            try:
                from db.models import ProjectAgentConfig

                logger.info(
                    "Creando nuevo agente (CACHE MISS)",
                    cache_key=cache_key,
                    cache_size=len(self._agents),
                    project_id=str(project_id) if project_id else None
                )

                # Si no se provee project_config, cargar default o desde DB
                if project_config is None and project_id:
                    async with self.db.get_session() as db_session:
                        from sqlalchemy.orm import selectinload
                        stmt = select(ProjectAgentConfig).options(
                            selectinload(ProjectAgentConfig.project)
                        ).where(
                            ProjectAgentConfig.project_id == project_id
                        )
                        result = await db_session.execute(stmt)
                        project_config = result.scalar_one_or_none()

                        if not project_config:
                            logger.warning(
                                "No se encontró ProjectAgentConfig, creando default",
                                project_id=str(project_id)
                            )
                            # Crear config default
                            project_config = await self._get_or_create_default_config(project_id, db_session)

                # Elegir tipo de agente basado en feature flag
                if self.settings.ENABLE_STATE_MACHINE:
                    # Usar RouterAgent (nueva arquitectura de agentes especializados)
                    from agents.router_agent import RouterAgent
                    agent = RouterAgent(
                        session_id=session_id,
                        store=self.store,
                        project_id=project_id,
                        project_config=project_config,
                        whatsapp_service=self.whatsapp_service,
                        verbose=self.settings.AGENT_VERBOSE
                    )
                    logger.info(
                        "RouterAgent (specialized) creado y cacheado",
                        cache_key=cache_key,
                        total_agents=len(self._agents)
                    )
                else:
                    # Usar DeyyAgent legacy (monolítico)
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
                        verbose=self.settings.AGENT_VERBOSE
                    )
                    logger.info(
                        "DeyyAgent (legacy) creado y cacheado",
                        cache_key=cache_key,
                        total_agents=len(self._agents)
                    )

                self._agents[cache_key] = agent
            except Exception as e:
                logger.error(
                    "Error creando agente",
                    cache_key=cache_key,
                    error=str(e),
                    exc_info=True
                )
                raise
        else:
            logger.info(
                "Reutilizando agente (CACHE HIT)",
                cache_key=cache_key,
                cache_size=len(self._agents)
            )

        return self._agents[cache_key]

    async def _get_or_create_default_config(
        self,
        project_id: uuid.UUID,
        session: AsyncSession
    ) -> ProjectAgentConfig:
        """
        Crea una configuración por defecto para un proyecto
        """
        from db.models import Project, ProjectAgentConfig

        # Verificar que el proyecto existe
        stmt = select(Project).where(Project.id == project_id)
        result = await session.execute(stmt)
        project = result.scalar_one_or_none()

        if not project:
            raise ValueError(f"Proyecto {project_id} no encontrado")

        # Crear config default
        config = ProjectAgentConfig(
            project_id=project_id,
            agent_name="DeyyAgent",
            system_prompt=f"Eres un asistente AI útil para el proyecto {project.name}.\n\nInstrucciones adicionales: Ayuda a los clientes con gestión de citas y consultas.",
            max_iterations=10,
            temperature=0.7,
            enabled_tools=["agendar_cita", "consultar_disponibilidad", "obtener_citas_cliente", "cancelar_cita"],
            calendar_enabled=False,
            global_agent_enabled=True
        )
        session.add(config)
        await session.flush()
        await session.commit()

        logger.info("Config default creada", project_id=str(project_id))
        return config

    def _extract_project_id(
        self,
        payload: Dict[str, Any],
        request: Request
    ) -> Optional[uuid.UUID]:
        """
        Extrae project_id del webhook.

        Orden de prioridad:
        1. Header X-Project-Id (UUID)
        2. Payload field 'project_id'
        3. Header X-API-Key (lookup project by api_key)
        4. Usar proyecto 'default' si existe
        5. None

        Args:
            payload: Payload del webhook
            request: FastAPI Request para headers

        Returns:
            UUID del proyecto o None
        """
        # Opción 1: Header X-Project-Id
        project_id_header = request.headers.get("X-Project-Id")
        if project_id_header:
            try:
                return uuid.UUID(project_id_header)
            except ValueError:
                logger.warning("X-Project-Id header inválido", value=project_id_header)

        # Opción 2: Payload project_id
        if "project_id" in payload:
            try:
                return uuid.UUID(payload["project_id"])
            except (ValueError, TypeError):
                logger.warning("project_id en payload inválido", value=payload.get("project_id"))

        # Opción 3: X-API-Key
        api_key = request.headers.get("X-API-Key")
        if api_key:
            # Buscar project por api_key hash
            # Por ahora no implementado; se podría hacer query
            logger.warning("X-API-Key auth no implementado aún, intentando default")
            # Continuamos para intentar default

        # Opción 4: Devolver None para usar default más adelante
        return None

    async def _health_check(self) -> Dict[str, Any]:
        """Health check endpoint"""
        return {
            "status": "healthy",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "version": "1.0.0"
        }

    async def _get_metrics(self) -> Dict[str, Any]:
        """Obtiene métricas del sistema"""
        metrics = {
            "active_sessions": len(self._agents),
            "timestamp": datetime.now(timezone.utc).isoformat()
        }
        return metrics

    async def _debug_agent(self, session_id: str) -> Dict[str, Any]:
        """
        Debug endpoint (solo DEBUG=true)
        """
        if not self.settings.DEBUG:
            raise HTTPException(status_code=404, detail="Not found")

        agent = self._agents.get(session_id)
        if not agent:
            raise HTTPException(status_code=404, detail="Agent not found")

        history = await self.memory_manager.get_history(session_id)

        return {
            "session_id": session_id,
            "initialized": agent._initialized,
            "message_count": len(history),
            "history": [
                {"type": type(msg).__name__, "content": msg.content[:100]}
                for msg in history[-10:]  # Últimos 10 mensajes
            ]
        }

    async def process_webhook(
        self,
        payload: Dict[str, Any],
        chain_type: str = 'unified'
    ) -> Dict[str, Any]:
        """
        Procesa un webhook de WhatsApp (versión para pruebas/programación)

        Args:
            payload: Dict con la estructura del webhook
            chain_type: Tipo de cadena ('unified' o 'processing')

        Returns:
            Dict con resultado del procesamiento incluyendo métricas
        """
        import time
        start_time = time.time()

        async with self.db.get_session() as session:
            # Parsear payload
            message_data = await self._parse_whatsapp_payload(payload)

            if not message_data:
                return {
                    "status": "ignored",
                    "reason": "Invalid payload",
                    "total_time_ms": 0,
                    "successful_links": 0,
                    "total_links": 0
                }

            # Crear o recuperar conversación
            conversation = await self._get_or_create_conversation(
                session,
                phone_number=message_data["sender"],
                platform="whatsapp"
            )

            # Guardar mensaje entrante
            inbound_msg = Message(
                conversation_id=conversation.id,
                direction="inbound",
                message_type=message_data["message_type"],
                content=message_data["message"],
                raw_payload=payload,
                processed=False
            )
            session.add(inbound_msg)
            await session.flush()

            # Procesar con agente
            agent = await self._get_or_create_agent(message_data["sender"])
            result = await agent.process_message(message_data["message"])

            # Marcar mensaje como procesado
            inbound_msg.processed = True
            inbound_msg.agent_response = result.get("output")
            session.add(inbound_msg)
            await session.commit()

            total_time = (time.time() - start_time) * 1000

            # Extraer herramientas usadas (tool_calls)
            tool_calls = result.get("tool_calls", [])
            # Formato: [{"tool": "agendar_cita", "input": {...}, "output": "..."}]
            tools_used = tool_calls

            # Extraer métricas de landchain si están disponibles (no aplica aquí)
            chain_result = result.get("chain_result", {})
            total_links = chain_result.get("total_links", 0) or len(tools_used)
            successful_links = chain_result.get("successful_links", 0) or len(tools_used)

            return {
                "status": "success",
                "conversation_id": str(conversation.id),
                "agent_response": result.get("response", ""),
                "tools_used": tools_used,
                "total_time_ms": total_time,
                "successful_links": successful_links,
                "total_links": total_links,
                "final_data": chain_result.get("data")
            }

    async def get_system_stats(self) -> Dict[str, Any]:
        """
        Obtiene estadísticas del sistema (métricas públicas)

        Returns:
            Dict con estadísticas
        """
        metrics = await self._get_metrics()
        metrics.update({
            "active_sessions": len(self._agents),
            "timestamp": datetime.now(timezone.utc).isoformat()
        })
        return metrics

    async def get_health_status(self) -> Dict[str, Any]:
        """
        Estado de salud del sistema

        Returns:
            Dict con información de salud
        """
        return {
            "status": "healthy",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "database": "connected" if self.db else "disconnected",
            "memory_backend": self.memory_manager.__class__.__name__ if self.memory_manager else None,
            "active_agents": len(self._agents),
            "version": "1.0.0"
        }

    async def shutdown(self):
        """Cierra recursos"""
        logger.info("Cerrando ArcadiumAPI")

        # Cerrar agentes
        for agent in self._agents.values():
            if hasattr(agent, 'memory_manager'):
                await agent.memory_manager.cleanup_expired_sessions()

        # Cerrar WhatsApp service
        if self.whatsapp_service:
            await self.whatsapp_service.disconnect()

        # Cerrar DB
        if self.db:
            await self.db.engine.dispose()

        logger.info("ArcadiumAPI cerrado")

    def start(self):
        """Inicia el servidor FastAPI (modo desarrollo)"""
        import uvicorn
        app = self.create_app()
        config = uvicorn.Config(
            app,
            host=self.settings.HOST,
            port=self.settings.PORT,
            workers=1,
            reload=False,
            log_level="info"
        )
        server = uvicorn.Server(config)
        try:
            print(f"🚀 Servidor iniciado en http://{self.settings.HOST}:{self.settings.PORT}")
            server.run()
        except KeyboardInterrupt:
            print("\n🛑 Servidor detenido")


# ============================================
# Main Entry Point
# ============================================

def create_app() -> FastAPI:
    """
    Factory function para crear la app
    Usada por uvicorn
    """
    api = ArcadiumAPI()
    return api.create_app()


async def main():
    """Punto de entrada para desarrollo"""
    settings = get_settings()

    api = ArcadiumAPI(settings)
    app = api.create_app()

    import uvicorn
    uvicorn.run(
        app,
        host=settings.HOST,
        port=settings.PORT,
        workers=settings.WORKERS
    )


if __name__ == "__main__":
    asyncio.run(main())


# Alias para compatibilidad con código existente
ArcadiumAutomation = ArcadiumAPI
