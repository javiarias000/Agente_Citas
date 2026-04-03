#!/usr/bin/env python3
"""
Arcadium Automation - Punto de entrada principal
FastAPI webhook para WhatsApp sin n8n
"""

import asyncio
import sys
from pathlib import Path

# Añadir directorio actual al path
sys.path.insert(0, str(Path(__file__).parent))

from core.orchestrator import ArcadiumAPI, get_settings


async def run_orchestrator():
    """Punto de entrada asincrónico"""
    settings = get_settings()

    api = ArcadiumAPI(settings)
    app = api.create_app()

    import uvicorn
    config = uvicorn.Config(
        app=app,
        host=settings.HOST,
        port=settings.PORT,
        workers=1,  # Lifespan solo es thread-safe con 1 worker
        log_level="info"
    )
    server = uvicorn.Server(config)
    await server.serve()


if __name__ == "__main__":
    try:
        asyncio.run(run_orchestrator())
    except KeyboardInterrupt:
        print("\n🛑 Shutdown requested by user")
        sys.exit(0)
    except Exception as e:
        print(f"❌ Fatal error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
