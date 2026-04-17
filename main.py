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


def create_app():
    """Factory function para Uvicorn"""
    settings = get_settings()
    api = ArcadiumAPI(settings)
    return api.create_app()


async def run_orchestrator():
    """Punto de entrada asincrónico"""
    settings = get_settings()

    api = ArcadiumAPI(settings)
    app = api.create_app()

    import uvicorn
    import socket
    import time

    # Intentar limpiar el puerto si está en uso (TIME_WAIT desde shutdown anterior)
    def wait_for_port_available(host: str, port: int, timeout: int = 5):
        """Espera a que el puerto esté disponible con reintentos"""
        start = time.time()
        while time.time() - start < timeout:
            try:
                sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                sock.bind((host, port))
                sock.close()
                return True
            except OSError as e:
                if "Address already in use" in str(e):
                    time.sleep(0.5)
                    continue
                raise
        return False

    # Esperar a que puerto esté disponible
    if not wait_for_port_available(settings.HOST, settings.PORT):
        print(f"❌ Puerto {settings.PORT} no está disponible después de esperar.")
        print(f"   Ejecuta: fuser -k {settings.PORT}/tcp")
        sys.exit(1)

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
