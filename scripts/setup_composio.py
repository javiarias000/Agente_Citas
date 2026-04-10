#!/usr/bin/env python3
"""
Setup de Composio para Google Calendar.

Pasos:
  1. Lee COMPOSIO_API_KEY del .env (o pide que lo ingreses)
  2. Busca el auth_config de Google Calendar en Composio
  3. Genera una URL de autenticación OAuth para conectar Google Calendar
  4. Espera a que el usuario complete la autenticación
  5. Verifica que la conexión funciona creando una sesión MCP de prueba

Uso:
    cd /home/jav/arcadium_automation
    source venv/bin/activate
    python scripts/setup_composio.py
"""

import asyncio
import os
import sys

# Agregar raíz del proyecto al path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv()


def get_or_ask(env_var: str, prompt: str, secret: bool = False) -> str:
    val = os.getenv(env_var, "").strip()
    if val:
        masked = val[:8] + "..." if len(val) > 8 else val
        print(f"  ✓ {env_var} = {masked}")
        return val
    print(f"  ✗ {env_var} no encontrado en .env")
    if secret:
        import getpass
        val = getpass.getpass(f"  Ingresa {prompt}: ").strip()
    else:
        val = input(f"  Ingresa {prompt}: ").strip()
    if not val:
        print(f"[ERROR] {env_var} es obligatorio.")
        sys.exit(1)
    return val


def update_env_file(key: str, value: str) -> None:
    """Agrega o actualiza una variable en el .env."""
    env_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env")
    lines = []
    found = False

    if os.path.exists(env_path):
        with open(env_path) as f:
            lines = f.readlines()
        for i, line in enumerate(lines):
            if line.startswith(f"{key}="):
                lines[i] = f"{key}={value}\n"
                found = True
                break

    if not found:
        lines.append(f"{key}={value}\n")

    with open(env_path, "w") as f:
        f.writelines(lines)

    print(f"  ✓ {key} guardado en .env")


async def main():
    print("=" * 60)
    print("  SETUP COMPOSIO — Google Calendar MCP")
    print("=" * 60)
    print()

    # ── Paso 1: API Key ───────────────────────────────────────────
    print("PASO 1: API Key de Composio")
    print("  Obtén tu API key en: https://app.composio.dev/settings")
    print()
    api_key = get_or_ask("COMPOSIO_API_KEY", "API Key de Composio", secret=True)
    print()

    # ── Importar cliente ──────────────────────────────────────────
    try:
        from composio_client import Composio
    except ImportError:
        print("[ERROR] composio-client no está instalado.")
        print("  Ejecuta: pip install composio-langchain langchain-mcp-adapters")
        sys.exit(1)

    client = Composio(api_key=api_key)

    # ── Paso 2: User ID ───────────────────────────────────────────
    print("PASO 2: User ID")
    print("  Es un identificador para asociar la conexión de Google Calendar.")
    print("  Usa tu email o cualquier string único (ej: 'clinica_arcadium').")
    print()
    user_id = get_or_ask("COMPOSIO_USER_ID", "User ID (ej: tu email o 'clinica_arcadium')")
    print()

    # ── Paso 3: Buscar auth_config de Google Calendar ─────────────
    print("PASO 3: Buscando configuración de Google Calendar en Composio...")
    try:
        auth_configs = client.auth_configs.list(toolkit_slug="googlecalendar")
        configs_list = list(auth_configs)
        print(f"  Configuraciones encontradas: {len(configs_list)}")
        for cfg in configs_list[:5]:
            print(f"    - ID: {cfg.id}  |  Name: {getattr(cfg, 'name', 'N/A')}  |  Status: {getattr(cfg, 'status', 'N/A')}")
    except Exception as e:
        print(f"  [WARN] No se pudo listar auth_configs: {e}")
        configs_list = []
    print()

    # ── Paso 4: Verificar si ya existe una conexión ───────────────
    print("PASO 4: Verificando conexiones existentes...")
    connection_exists = False
    try:
        connected = client.connected_accounts.list(
            toolkit_slugs=["googlecalendar"],
            user_ids=[user_id],
            statuses=["ACTIVE"],
        )
        connected_list = list(connected)
        user_connections = connected_list  # ya filtrado por user_id
        if user_connections:
            print(f"  ✓ Ya existe una conexión de Google Calendar para user_id='{user_id}'")
            for conn in user_connections:
                print(f"    - Connection ID: {conn.id}  |  Status: {getattr(conn, 'status', 'N/A')}")
            connection_exists = True
        else:
            print(f"  ✗ No hay conexión de Google Calendar para user_id='{user_id}'")
            print(f"    (Encontradas {len(connected_list)} conexiones en total, ninguna para este user_id)")
    except Exception as e:
        print(f"  [WARN] No se pudo verificar conexiones: {e}")
    print()

    # ── Paso 5: Crear link de autenticación ───────────────────────
    if not connection_exists:
        print("PASO 5: Autenticando Google Calendar...")

        auth_config_id = None
        if configs_list:
            # Usar el primer auth_config de googlecalendar
            auth_config_id = configs_list[0].id
            print(f"  Usando auth_config: {auth_config_id}")
        else:
            # Intentar usar el slug directamente
            print("  Intentando con slug 'googlecalendar' directamente...")
            auth_config_id = "googlecalendar"

        try:
            link_resp = client.link.create(
                auth_config_id=auth_config_id,
                user_id=user_id,
            )
            auth_url = link_resp.url if hasattr(link_resp, 'url') else str(link_resp)

            print()
            print("  " + "=" * 56)
            print("  ABRE ESTE LINK EN TU NAVEGADOR PARA AUTENTICAR:")
            print()
            print(f"  {auth_url}")
            print()
            print("  " + "=" * 56)
            print()
            input("  Presiona ENTER después de completar la autenticación en el navegador...")
            print()
        except Exception as e:
            print(f"  [ERROR] No se pudo crear el link de autenticación: {e}")
            print()
            print("  Alternativa manual:")
            print("  1. Ve a https://app.composio.dev")
            print("  2. Dashboard → Apps → Google Calendar")
            print("  3. Haz clic en 'Connect'")
            print("  4. Completa la autenticación OAuth con Google")
            print()
            input("  Presiona ENTER cuando hayas completado la autenticación en el dashboard...")
            print()
    else:
        print("PASO 5: Conexión ya existe — omitiendo autenticación.")
        print()

    # ── Paso 6: Guardar variables en .env ─────────────────────────
    print("PASO 6: Guardando configuración en .env...")
    update_env_file("COMPOSIO_API_KEY", api_key)
    update_env_file("COMPOSIO_USER_ID", user_id)
    print()

    # ── Paso 7: Probar sesión MCP ─────────────────────────────────
    print("PASO 7: Probando sesión MCP con Google Calendar...")
    try:
        session = client.tool_router.session.create(
            user_id=user_id,
            toolkits={"enable": ["googlecalendar"]},
        )
        print(f"  ✓ Sesión MCP creada: {session.session_id}")
        print(f"  ✓ URL MCP: {session.mcp.url[:60]}...")
        print(f"  ✓ Tools disponibles: {session.tool_router_tools[:5]}{'...' if len(session.tool_router_tools) > 5 else ''}")
        print()

        # ── Paso 8: Cargar tools MCP y verificar Google Calendar ──
        print("PASO 8: Cargando tools de Google Calendar via MCP...")
        from langchain_mcp_adapters.client import MultiServerMCPClient

        mcp_client = MultiServerMCPClient(
            {
                "googlecalendar": {
                    "transport": "streamable_http",
                    "url": session.mcp.url,
                    "headers": {"x-api-key": api_key},
                }
            }
        )
        tools = await mcp_client.get_tools()
        tool_names = [t.name for t in tools]

        gcal_tools = [n for n in tool_names if "GOOGLECALENDAR" in n.upper() or "CALENDAR" in n.upper()]
        print(f"  ✓ Total tools cargados: {len(tool_names)}")
        print(f"  ✓ Google Calendar tools: {gcal_tools}")
        print()

        if not gcal_tools:
            print("  [WARN] No se encontraron tools de Google Calendar.")
            print("  Verifica que la autenticación con Google se completó correctamente.")
            print(f"  Todos los tools disponibles: {tool_names}")
        else:
            print("=" * 60)
            print("  ✓ SETUP COMPLETADO EXITOSAMENTE")
            print()
            print(f"  COMPOSIO_API_KEY = {api_key[:8]}...")
            print(f"  COMPOSIO_USER_ID = {user_id}")
            print()
            print("  Google Calendar está conectado y listo para usar.")
            print("  Ahora puedes arrancar el sistema con: ./run.sh start")
            print("=" * 60)

    except Exception as e:
        print(f"  [ERROR] No se pudo crear sesión MCP: {e}")
        print()
        print("  Posibles causas:")
        print("  - La autenticación con Google no se completó")
        print("  - La API key es inválida")
        print("  - El user_id no tiene conexiones de Google Calendar")
        print()
        print("  Intenta nuevamente después de:")
        print("  1. Verificar tu API key en https://app.composio.dev/settings")
        print("  2. Conectar Google Calendar en https://app.composio.dev/apps")
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
