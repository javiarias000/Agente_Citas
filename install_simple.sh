#!/bin/bash
#
# Instalador simplificado de Arcadium Automation
# No requiere sudo - usa Python user install
#

set -e

echo "========================================"
echo "ARCADEUM AUTOMATION - INSTALADOR SIMPLE"
echo "========================================"
echo ""

# Colores
GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
NC='\033[0m'

# Detectar si python3-venv está disponible
if python3 -m venv --help &>/dev/null; then
    echo "🔍 Creando entorno virtual..."
    python3 -m venv venv
    source venv/bin/activate
    echo -e "${GREEN}✓ Entorno virtual creado${NC}"
else
    echo -e "${YELLOW}⚠ python3-venv no disponible, usando Python global${NC}"
    echo "   (considera instalar: sudo apt install python3.12-venv)"
    VENV_ACTIVATED=false
fi

# Instalar dependencias
echo ""
echo "📦 Instalando dependencias..."

if [ "${VENV_ACTIVATED:-true}" = "true" ]; then
    pip install --upgrade pip setuptools wheel
    pip install -r requirements.txt
else
    pip3 install --user --upgrade pip setuptools wheel
    # NOTA: instalación user-site puede causar problemas con módulos del sistema
    # Mejor sugerir instalar venv
    echo ""
    echo -e "${RED}❌ No se puede continuar sin entorno virtual${NC}"
    echo "   Instala python3-venv:"
    echo "   sudo apt update && sudo apt install -y python3.12-venv"
    exit 1
fi

# Configurar .env
echo ""
if [ ! -f .env ]; then
    cp .env.example .env
    echo -e "${GREEN}✓ .env creado (recuerda editarlo!)${NC}"
fi

# Directorios
mkdir -p logs data

echo ""
echo "========================================"
echo -e "${GREEN}✅ INSTALACIÓN COMPLETADA${NC}"
echo "========================================"
echo ""
echo "Próximos pasos:"
echo "  1. Edita .env con tus claves:"
echo "     nano .env"
echo ""
echo "  2. Valida la configuración:"
echo "     ./run.sh validate"
echo ""
echo "  3. Ejecuta la demo:"
echo "     ./run.sh demo"
echo ""
echo "  4. Inicia el sistema:"
echo "     ./run.sh start"
echo ""
