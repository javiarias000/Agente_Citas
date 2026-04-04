#!/bin/bash
#
# Ejecuta Arcadium Automation (sin n8n)
# Uso: ./run.sh [comando]
#

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# Colores
GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

command="${1:-start}"

# Verificar venv
if [ ! -d "venv" ]; then
    echo -e "${RED}❌ Entorno virtual no encontrado${NC}"
    echo "   Ejecuta primero: bash install_simple.sh"
    exit 1
fi

# Activar venv
source venv/bin/activate

# Cargar .env si existe
if [ -f ".env" ]; then
    export $(grep -v '^#' .env | grep '=' | xargs) 2>/dev/null || true
fi

# Ejecutar comando
case "$command" in
    start|run)
        echo -e "${BLUE}🚀 Iniciando Arcadium API...${NC}"
        python main.py
        ;;

    dev)
        echo -e "${BLUE}🛠️  Iniciando en modo desarrollo con reload...${NC}"
        uvicorn main:create_app --host ${HOST:-0.0.0.0} --port ${PORT:-8000} --reload
        ;;

    test)
        shift
        echo -e "${BLUE}🧪 Ejecutando tests...${NC}"
        pytest "$@" || true
        ;;

    shell|console)
        echo -e "${BLUE}🐚 Abriendo consola interactiva...${NC}"
        python -i -c "from core.orchestrator import ArcadiumAPI; import asyncio; api = ArcadiumAPI(); print('\n✅ API lista. Usa: asyncio.run(api.initialize())')"
        ;;

    validate|check)
        echo -e "${BLUE}🔍 Validando configuración...${NC}"
        python validate_configs.py
        ;;

    migrate|db-migrate)
        echo -e "${BLUE}🗃️  Ejecutando migraciones de base de datos...${NC}"
        python db/migrate.py
        ;;

    verify|db-status)
        echo -e "${BLUE}🔍 Verificando estado de la base de datos...${NC}"
        python db/verify.py
        ;;

    db-reset)
        echo -e "${RED}⚠️  Reseteando base de datos (¡PELIGRO!)${NC}"
        python db/migrate.py --reset
        ;;

    example)
        echo -e "${BLUE}📨 Enviando mensaje de prueba...${NC}"
        python examples/test_webhook.py
        ;;

    docker-build)
        echo -e "${BLUE}🐳 Construyendo imagen Docker...${NC}"
        docker build -t arcadium-api .
        ;;

    docker-run)
        echo -e "${BLUE}🐳 Ejecutando con Docker Compose...${NC}"
        docker-compose up
        ;;

    logs)
        echo -e "${BLUE}📄 Últimos logs${NC}"
        if [ -f logs/arcadium_automation.log ]; then
            tail -f logs/arcadium_automation.log
        else
            echo -e "${YELLOW}⚠️  No hay logs aún${NC}"
            echo "   Inicia el sistema con: ./run.sh start"
        fi
        ;;

    *)
        echo -e "${YELLOW}Uso: $0 [comando]${NC}"
        echo ""
        echo "Comandos:"
        echo "  start/run     Iniciar API (modo producción)"
        echo "  dev           Iniciar con reload (desarrollo)"
        echo "  test          Ejecutar tests"
        echo "  validate      Validar configuración"
        echo "  migrate       Ejecutar migraciones de DB"
        echo "  verify        Verificar estado de DB"
        echo "  db-reset      ⚠️  Resetear DB (pierde datos)"
        echo "  example       Enviar mensaje de prueba"
        echo "  shell         Consola interactiva"
        echo "  logs          Ver logs en vivo"
        echo "  docker-build  Construir imagen Docker"
        echo "  docker-run    Ejecutar con Docker Compose"
        echo ""
        echo "Ejemplos:"
        echo "  ./run.sh start"
        echo "  ./run.sh dev"
        echo "  ./run.sh migrate     (crear tablas en DB)"
        echo "  ./run.sh verify      (ver tablas creadas)"
        echo "  ./run.sh db-reset    (¡CUIDADO! borra todo)"
        exit 1
        ;;
esac
