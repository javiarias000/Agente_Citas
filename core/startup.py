#!/usr/bin/env python3
"""
Startup validation and initialization hooks.
Runs before the app starts to validate configuration and dependencies.
"""

import os
import sys
from typing import List, Tuple
import structlog

logger = structlog.get_logger("startup")


def validate_environment() -> Tuple[bool, List[str]]:
    """
    Valida que todas las variables de entorno requeridas estén configuradas.
    Retorna (success: bool, errors: List[str])
    """
    from core.config import get_settings

    settings = get_settings()
    errors: List[str] = []

    # CRÍTICAS
    critical = [
        ("OPENAI_API_KEY", settings.OPENAI_API_KEY),
        ("DATABASE_URL", settings.DATABASE_URL),
        ("WHATSAPP_API_URL", settings.WHATSAPP_API_URL),
    ]

    for name, value in critical:
        if not value:
            errors.append(f"❌ CRÍTICA: {name} no configurado")

    # REQUERIDAS EN PRODUCCIÓN
    if not settings.DEBUG:
        prod_required = [
            ("WEBHOOK_SECRET", settings.WEBHOOK_SECRET),
            ("API_KEY", settings.API_KEY),
        ]
        for name, value in prod_required:
            if not value:
                errors.append(f"⚠️  PRODUCCIÓN: {name} requerido en DEBUG=false")

    # OPCIONALES (solo warnings)
    optional = [
        ("GOOGLE_CALENDAR_ENABLED", settings.GOOGLE_CALENDAR_ENABLED, "Para calendario"),
        ("USE_LANGGRAPH", settings.USE_LANGGRAPH, "Para LangGraph"),
    ]

    for name, value, description in optional:
        if not value:
            logger.warning(f"Opcional deshabilitado: {name} ({description})")

    return len(errors) == 0, errors


async def validate_database() -> Tuple[bool, List[str]]:
    """
    Verifica que la BD esté accesible y las migraciones estén aplicadas.
    """
    from sqlalchemy import text
    from core.config import get_settings
    from core.orchestrator import Database

    errors = []
    try:
        settings = get_settings()
        db = Database(settings.DATABASE_URL)

        # Test connection
        async with db.engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
            logger.info("✅ Database connection OK")

        # Check required tables
        async with db.engine.begin() as conn:
            result = await conn.execute(
                text("""
                SELECT table_name FROM information_schema.tables
                WHERE table_schema = 'public'
                """)
            )
            tables = {row[0] for row in result.fetchall()}
            required = {"conversations", "messages", "appointments"}
            missing = required - tables
            if missing:
                errors.append(f"Tablas faltantes: {missing}. Ejecuta: alembic upgrade head")

        await db.engine.dispose()
        return len(errors) == 0, errors
    except Exception as e:
        return False, [f"Error BD: {str(e)}"]


def validate_external_services() -> Tuple[bool, List[str]]:
    """
    Verifica que los servicios externos estén configurados (no conectados).
    """
    from core.config import get_settings

    settings = get_settings()
    errors = []
    warnings = []

    # OpenAI
    if not settings.OPENAI_API_KEY.startswith("sk-"):
        warnings.append("⚠️  OPENAI_API_KEY no parece válido (no comienza con 'sk-')")

    # Google Calendar
    if settings.GOOGLE_CALENDAR_ENABLED:
        creds_path = settings.GOOGLE_CALENDAR_CREDENTIALS_PATH
        if not os.path.exists(creds_path):
            errors.append(f"Google credentials no encontrado: {creds_path}")

    # WhatsApp
    if not settings.WHATSAPP_API_URL.startswith("http"):
        errors.append("WHATSAPP_API_URL no es una URL válida")

    for warning in warnings:
        logger.warning(warning)

    return len(errors) == 0, errors


async def validate_migrations() -> Tuple[bool, List[str]]:
    """
    Verifica que todas las migraciones hayan sido aplicadas.
    """
    from sqlalchemy import text
    from core.config import get_settings
    from core.orchestrator import Database

    try:
        settings = get_settings()
        db = Database(settings.DATABASE_URL)

        async with db.engine.connect() as conn:
            result = await conn.execute(
                text("SELECT name FROM schema_migrations ORDER BY id DESC LIMIT 1")
            )
            row = result.fetchone()
            if row:
                logger.info(f"✅ Última migración aplicada: {row[0]}")
                return True, []
            else:
                return False, ["No se encontraron migraciones aplicadas. Ejecuta: ./run.sh migrate"]

        await db.engine.dispose()
    except Exception as e:
        # Si la tabla no existe aún, probablemente es la primera ejecución
        if "schema_migrations" in str(e):
            return False, ["Migraciones no inicializadas. Ejecuta: ./run.sh migrate"]
        return False, [f"Error verificando migraciones: {str(e)}"]


async def run_startup_checks() -> bool:
    """
    Ejecuta todos los checks de startup. Retorna True si todo está OK.
    """
    import asyncio

    logger.info("🚀 Ejecutando checks de startup...")

    checks = [
        ("Entorno", validate_environment, False),  # sync
        ("Base de datos", validate_database, True),  # async
        ("Servicios externos", validate_external_services, False),  # sync
        ("Migraciones", validate_migrations, True),  # async
    ]

    all_passed = True
    total_errors = 0

    for check_name, check_func, is_async in checks:
        try:
            logger.info(f"📋 Validando {check_name}...")
            if is_async:
                passed, errors = await check_func()
            else:
                passed, errors = check_func()

            if passed:
                logger.info(f"✅ {check_name}: OK")
            else:
                logger.error(f"❌ {check_name}: FALLOS")
                for error in errors:
                    logger.error(f"   {error}")
                    total_errors += 1
                all_passed = False

        except Exception as e:
            logger.error(f"❌ {check_name}: Excepción", error=str(e))
            all_passed = False

    if all_passed:
        logger.info("✅ Todos los checks pasados. App lista para arrancar.")
    else:
        logger.error(f"❌ {total_errors} errores encontrados. Corrige y reinicia.")
        sys.exit(1)

    return all_passed
