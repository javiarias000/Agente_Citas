#!/usr/bin/env python3
"""
Configuración centralizada con Pydantic v2
Sin dependencias circulares, lista para producción
"""

from typing import Optional, List
from pydantic_settings import BaseSettings
from pydantic import Field, field_validator
import json
import os


class Settings(BaseSettings):
    """Configuración completa de la aplicación"""

    # === Aplicación ===
    APP_NAME: str = Field(default="Arcadium Automation", description="Nombre de la app")
    DEBUG: bool = Field(default=False, description="Modo debug")
    HOST: str = Field(default="0.0.0.0", description="Host del servidor")
    PORT: int = Field(default=8000, description="Puerto del servidor")
    WORKERS: int = Field(default=4, description="Workers de Uvicorn")

    # === WhatsApp API (Evolution API recomendado) ===
    WHATSAPP_API_URL: str = Field(
        default="",
        description="URL base de Evolution API (ej: https://evolution-api.example.com)"
    )
    WHATSAPP_API_TOKEN: Optional[str] = Field(
        default=None,
        description="Token de Evolution API (opcional según instancia)"
    )
    WHATSAPP_INSTANCE_NAME: Optional[str] = Field(
        default=None,
        description="Nombre de la instancia en Evolution API"
    )
    WHATSAPP_ADMIN_NUMBER: Optional[str] = Field(
        default=None,
        description="Número de admin para notificaciones (formato: 1234567890@s.whatsapp.net)"
    )

    # Chatwoot API
    CHATWOOT_API_URL: str = Field(
        default="",
        description="URL base de Chatwoot API (ej: https://app.chatwoot.com)"
    )
    CHATWOOT_API_TOKEN: Optional[str] = Field(
        default=None,
        description="Personal Access Token o API Key de Chatwoot"
    )
    CHATWOOT_ACCOUNT_ID: Optional[int] = Field(
        default=None,
        description="ID de la cuenta en Chatwoot"
    )
    CHATWOOT_INBOX_ID: Optional[int] = Field(
        default=None,
        description="ID del inbox (canal) en Chatwoot"
    )
    CHATWOOT_WEBHOOK_SECRET: Optional[str] = Field(
        default=None,
        description="Secreto para verificar webhooks de Chatwoot (opcional)"
    )

    # Base de datos
    DATABASE_URL: str = Field(
        default="postgresql+psycopg2://user:pass@localhost:5432/arcadium",
        description="URL de PostgreSQL"
    )
    DB_POOL_SIZE: int = Field(default=10, description="Pool size de conexiones")
    DB_MAX_OVERFLOW: int = Field(default=20, description="Max overflow del pool")

    # Memoria / State
    USE_POSTGRES_FOR_MEMORY: bool = Field(
        default=True,
        description="Usar PostgreSQL para memoria (false = InMemory)"
    )
    MEMORY_TABLE_PREFIX: str = Field(
        default="langchain_memory",
        description="Prefijo para tablas de memoria"
    )
    SESSION_EXPIRY_HOURS: int = Field(
        default=24,
        description="Tiempo de expiración de sesiones en horas"
    )

    # LLM / OpenAI
    OPENAI_API_KEY: str = Field(
        default="",
        description="API key de OpenAI"
    )
    OPENAI_MODEL: str = Field(
        default="gpt-4o-mini",
        description="Modelo de OpenAI"
    )
    OPENAI_TEMPERATURE: float = Field(
        default=0.7,
        ge=0.0,
        le=2.0,
        description="Temperatura del modelo"
    )
    OPENAI_MAX_TOKENS: Optional[int] = Field(
        default=None,
        description="Máximo de tokens"
    )
    OPENAI_TIMEOUT: int = Field(
        default=30,
        description="Timeout en segundos para llamadas a OpenAI"
    )

    # Configuración del Agente
    AGENT_MAX_ITERATIONS: int = Field(
        default=10,
        description="Iteraciones máximas del agente"
    )
    AGENT_VERBOSE: bool = Field(
        default=False,
        description="Logs detallados del agente"
    )
    AGENT_SYSTEM_PROMPT: Optional[str] = Field(
        default=None,
        description="Prompt del sistema personalizado"
    )

    # State Machine (nuevo en v2.1)
    ENABLE_STATE_MACHINE: bool = Field(
        default=True,
        description="Habilitar State Machine pattern para DeyyAgent (usa SupportState + middleware)"
    )

    # LangGraph (migración v3.0)
    USE_LANGGRAPH: bool = Field(
        default=False,
        description="Usar ArcadiumAgent (LangGraph) en lugar de DeyyAgent/RouterAgent. "
                    "Feature flag para migración sin downtime."
    )
    LANGGRAPH_MODEL: str = Field(
        default="gpt-4o-mini",
        description="Modelo LLM para agentes LangGraph"
    )
    LANGGRAPH_TEMPERATURE: float = Field(
        default=0.5,
        description="Temperatura del LLM para agentes LangGraph"
    )

    # Logging
    LOG_LEVEL: str = Field(
        default="INFO",
        description="Nivel de logging (DEBUG, INFO, WARNING, ERROR)"
    )
    LOG_FORMAT: str = Field(
        default="json",
        description="Formato de logs: json o text"
    )

    # Monitoreo
    ENABLE_METRICS: bool = Field(
        default=True,
        description="Habilitar métricas Prometheus"
    )
    METRICS_PORT: int = Field(
        default=9090,
        description="Puerto para métricas"
    )

    # Seguridad
    WEBHOOK_SECRET: Optional[str] = Field(
        default=None,
        description="Secreto para verificar webhooks (opcional)"
    )
    CORS_ORIGINS: List[str] = Field(
        default=["*"],
        description="Origins permitidos en CORS"
    )

    # Rate Limiting
    RATE_LIMIT_REQUESTS: int = Field(
        default=100,
        description="Peticiones por minuto por IP"
    )

    # Redis (opcional para cache)
    REDIS_URL: Optional[str] = Field(
        default=None,
        description="URL de Redis para cache"
    )

    # Google Calendar / OAuth
    GOOGLE_CALENDAR_ENABLED: bool = Field(
        default=False,
        description="Habilitar integración con Google Calendar"
    )
    GOOGLE_CALENDAR_CREDENTIALS_PATH: str = Field(
        default="./credentials/google_credentials.json",
        description="Ruta al archivo de credenciales OAuth2 de Google"
    )
    GOOGLE_CALENDAR_DEFAULT_ID: str = Field(
        default="primary",
        description="ID del calendario (email o 'primary')"
    )
    GOOGLE_CALENDAR_TIMEZONE: str = Field(
        default="America/Guayaquil",
        description="Timezone para eventos"
    )
    GOOGLE_REDIRECT_URI: Optional[str] = Field(
        default=None,
        description="URI de redirección para OAuth flow (ej: http://localhost:8000/oauth2callback)"
    )
    MCP_GOOGLE_CALENDAR_ENDPOINT: Optional[str] = Field(
        default=None,
        description="Endpoint del MCP server para Google Calendar"
    )

    # Supabase (para vectorstore)
    SUPABASE_URL: Optional[str] = Field(
        default=None,
        description="URL de Supabase para pgvector"
    )
    SUPABASE_ANON_KEY: Optional[str] = Field(
        default=None,
        description="Clave anónima de Supabase"
    )

    @field_validator('WHATSAPP_API_URL')
    @classmethod
    def validate_whatsapp_url(cls, v: str) -> str:
        """Valida y normaliza la URL de Evolution API"""
        if not v:
            raise ValueError('WHATSAPP_API_URL es requerido')
        # Eliminar trailing slash para evitar dobles slashes en endpoints
        return v.rstrip('/')

    @field_validator('OPENAI_API_KEY')
    @classmethod
    def validate_openai_key(cls, v: str) -> str:
        """Valida que la API key de OpenAI esté presente si se usa LLM"""
        if not v:
            raise ValueError('OPENAI_API_KEY es requerido')
        return v

    @field_validator('CORS_ORIGINS', mode='before')
    @classmethod
    def validate_cors_origins(cls, v):
        """Parsea CORS_ORIGINS desde string JSON o lista"""
        if isinstance(v, list):
            return v
        if isinstance(v, str):
            try:
                # Intentar parsear como JSON (ej: '["*"]' o '["http://example.com","https://example.com"]')
                parsed = json.loads(v)
                if isinstance(parsed, list):
                    return parsed
                # Si es string simple (ej: "*"), convertir a lista
                return [v]
            except json.JSONDecodeError:
                # Si falla, tratar como string simple y convertir a lista
                return [v]
        return v

    class Config:
        env_file = ".env"
        case_sensitive = True
        extra = "ignore"

    def __str__(self) -> str:
        return f"Settings(app={self.APP_NAME}, debug={self.DEBUG})"


# Instancia global (para compatibilidad)
_settings: Optional[Settings] = None


def get_settings() -> Settings:
    """
    Obtiene la instancia de configuración.
    Usar esta función en lugar de instanciar directamente.
    """
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings


# Instancia global por defecto (para compatibilidad con imports existentes)
settings = get_settings()
