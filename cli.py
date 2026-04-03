#!/usr/bin/env python3
"""
CLI para Arcadium Automation

Uso:
    python -m arcadium_automation [comando] [opciones]

Comandos:
    start           Inicia el orquestador completo
    process         Procesa un webhook manualmente
    test            Ejecuta suite de tests
    status          Muestra estado del sistema
    metrics         Muestra métricas en tiempo real
    validate        Valida archivos de configuración
"""

import asyncio
import json
import sys
import argparse
from pathlib import Path
from typing import Dict, Any

# Añadir directorio actual al path
sys.path.insert(0, str(Path(__file__).parent))

from core.orchestrator import ArcadiumAutomation
from core.config import get_settings
from utils.logger import setup_logger
import structlog

logger = structlog.get_logger("cli")


def start_command(args: argparse.Namespace):
    """Inicia el orquestador completo"""
    print("🚀 Iniciando Arcadium Automation...")
    print(f"   Configuración: {args.config or 'default'}")

    if args.config:
        # TODO: Cargar configuración personalizada
        pass

    try:
        # Importar y ejecutar main() del módulo principal
        from main import run_orchestrator
        asyncio.run(run_orchestrator())
    except KeyboardInterrupt:
        print("\n🛑 Detenido por usuario")
    except Exception as e:
        print(f"❌ Error: {e}")
        sys.exit(1)


def process_command(args: argparse.Namespace):
    """Procesa webhook manualmente"""
    print("📦 Procesando webhook...")

    async def run():
        orchestrator = ArcadiumAutomation()
        await orchestrator.initialize()

        # Cargar payload
        if args.file:
            with open(args.file, 'r') as f:
                payload = json.load(f)
        elif args.json:
            payload = json.loads(args.json)
        else:
            print("❌ Debes especificar --file o --json")
            sys.exit(1)

        # Procesar
        try:
            result = await orchestrator.process_webhook(
                payload=payload,
                chain_type=args.chain
            )

            print(json.dumps(result, indent=2, ensure_ascii=False))
            print(f"\n✅ Procesado: {result['status']} ({result['total_time_ms']:.2f}ms)")

        except Exception as e:
            print(f"❌ Error: {e}")
            sys.exit(1)

        finally:
            await orchestrator.shutdown()

    asyncio.run(run())


def test_command(args: argparse.Namespace):
    """Ejecuta suite de tests"""
    print("🧪 Ejecutando tests...")

    import subprocess
    cmd = ["pytest", "tests/", "-v"]

    if args.coverage:
        cmd.extend(["--cov=.", "--cov-report=html"])

    if args.junit:
        cmd.extend(["--junitxml=test-results.xml"])

    result = subprocess.run(cmd)

    sys.exit(result.returncode)


def status_command(args: argparse.Namespace):
    """Muestra estado del sistema"""
    print("📊 Estado del Sistema Arcadium")
    print("=" * 50)

    async def get_status():
        orchestrator = ArcadiumAutomation()
        try:
            await orchestrator.initialize()

            # Estado general
            health = await orchestrator.get_health_status()
            print(f"\n🏥 Salud: {health.get('status', 'unknown').upper()}")

            if 'components' in health:
                print("\n   Componentes:")
                for name, status in health['components'].items():
                    print(f"     {name}: {status.get('status', 'unknown')} "
                          f"({status.get('success_rate', 0):.1f}% éxito)")

            if 'resources' in health:
                print("\n   Recursos:")
                res = health['resources']
                print(f"     CPU: {res.get('cpu_percent', 0):.1f}%")
                print(f"     Memoria: {res.get('memory_percent', 0):.1f}%")

            # Stats
            stats = await orchestrator.get_system_stats()
            print("\n📈 Estadísticas:")
            for chain_name, chain_stats in stats.get('chains', {}).items():
                print(f"   {chain_name}:")
                print(f"     Ejecuciones: {chain_stats.get('total_executions', 0)}")
                print(f"     Éxito: {chain_stats.get('successful_executions', 0)}")
                print(f"     Fallos: {chain_stats.get('failed_executions', 0)}")
                print(f"     Tiempo promedio: {chain_stats.get('avg_time_ms', 0):.1f}ms")

        finally:
            await orchestrator.shutdown()

    asyncio.run(get_status())


def metrics_command(args: argparse.Namespace):
    """Muestra métricas en tiempo real"""
    print("📊 Métricas en tiempo real (Ctrl+C para detener)")
    print()

    async def stream_metrics():
        orchestrator = ArcadiumAutomation()
        try:
            await orchestrator.initialize()

            while True:
                stats = await orchestrator.get_system_stats()
                health = await orchestrator.get_health_status()

                # Limpiar pantalla
                print("\033[2J\033[H", end="")

                print("╔═══════════════════════════════════════════════╗")
                print("║   ARCADEUM AUTOMATION - MÉTRICAS EN VIVO    ║")
                print("╚═══════════════════════════════════════════════╝")
                print()

                print(f"🕐 {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
                print(f"🏥 Salud: {health.get('status', 'unknown').upper()}")
                print()

                print("📊 Cadenas:")
                for chain_name, chain_stats in stats.get('chains', {}).items():
                    total = chain_stats.get('total_executions', 0)
                    success = chain_stats.get('successful_executions', 0)
                    failed = chain_stats.get('failed_executions', 0)
                    rate = chain_stats.get('success_rate', 0)

                    print(f"  {chain_name}:")
                    print(f"    Total: {total} | ✓ {success} | ✗ {failed} | Tasa: {rate:.1f}%")
                    print(f"    Tiempo prom: {chain_stats.get('avg_time_ms', 0):.1f}ms")

                if 'system' in stats:
                    print()
                    print("💻 Sistema:")
                    sys_stats = stats['system']
                    print(f"  CPU: {sys_stats.get('cpu_percent', 0):.1f}%")
                    print(f"  Memoria: {sys_stats.get('memory_percent', 0):.1f}%")
                    print(f"  Disco: {sys_stats.get('disk_usage_percent', 0):.1f}%")

                print()
                print("─" * 50)

                await asyncio.sleep(2)

        except KeyboardInterrupt:
            print("\n\n🛑 Monitoreo detenido")
        finally:
            await orchestrator.shutdown()

    asyncio.run(stream_metrics())


def validate_command(args: argparse.Namespace):
    """Valida archivos de configuración"""
    print("🔍 Validando configuración...")

    errors = []

    # Obtener settings
    settings = get_settings()

    # Validar JSON de workflows
    workflows = [
        ("Workflow Unificado", settings.WORKFLOW_JSON_PATH),
        ("Workflow Procesamiento", settings.PROCESSING_JSON_PATH)
    ]

    for name, path in workflows:
        if not Path(path).exists():
            errors.append(f"  ❌ {name} no existe: {path}")
        else:
            try:
                with open(path, 'r') as f:
                    json.load(f)
                print(f"  ✓ {name}: OK")
            except json.JSONDecodeError as e:
                errors.append(f"  ❌ {name}: JSON inválido - {e}")

    # Validar variables de entorno
    required_env = ['N8N_BASE_URL', 'OPENAI_API_KEY']
    for var in required_env:
        if not getattr(settings, var, None):
            errors.append(f"  ⚠ Variable entorno no configurada: {var}")
        else:
            print(f"  ✓ {var}: configurado")

    if errors:
        print("\n❌ Errores encontrados:")
        for error in errors:
            print(error)
        sys.exit(1)
    else:
        print("\n✅ Configuración válida")
        sys.exit(0)


def main():
    parser = argparse.ArgumentParser(
        description="Arcadium Automation CLI",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Ejemplos:
  # Iniciar sistema
  python -m arcadium_automation start

  # Procesar webhook de prueba
  python -m arcadium_automation process --file test_payload.json

  # Ver estado
  python -m arcadium_automation status

  # Monitoreo en vivo
  python -m arcadium_automation metrics

  # Validar configuración
  python -m arcadium_automation validate

  # Ejecutar tests
  python -m arcadium_automation test --coverage
        """
    )

    subparsers = parser.add_subparsers(dest='command', help='Comando a ejecutar')

    # Start
    start_parser = subparsers.add_parser('start', help='Inicia el orquestador')
    start_parser.add_argument('--config', help='Archivo de configuración')
    start_parser.set_defaults(func=start_command)

    # Process
    process_parser = subparsers.add_parser('process', help='Procesa webhook')
    process_group = process_parser.add_mutually_exclusive_group(required=True)
    process_group.add_argument('--file', '-f', help='Archivo JSON con payload')
    process_group.add_argument('--json', '-j', help='JSON string del payload')
    process_parser.add_argument('--chain', default='unified', choices=['unified', 'processing'],
                                help='Tipo de cadena')
    process_parser.set_defaults(func=process_command)

    # Test
    test_parser = subparsers.add_parser('test', help='Ejecuta tests')
    test_parser.add_argument('--coverage', action='store_true', help='Con coverage')
    test_parser.add_argument('--junit', action='store_true', help='Generar JUnit XML')
    test_parser.set_defaults(func=test_command)

    # Status
    status_parser = subparsers.add_parser('status', help='Estado del sistema')
    status_parser.set_defaults(func=status_command)

    # Metrics
    metrics_parser = subparsers.add_parser('metrics', help='Métricas en tiempo real')
    metrics_parser.set_defaults(func=metrics_command)

    # Validate
    validate_parser = subparsers.add_parser('validate', help='Valida configuración')
    validate_parser.set_defaults(func=validate_command)

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(1)

    args.func(args)


if __name__ == "__main__":
    main()
