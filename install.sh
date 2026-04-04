#!/bin/bash
#
# Instalador de Arcadium Automation
# Ejecuta: bash install.sh
#

set -e  # Salir en error

echo "========================================"
echo "ARCADEUM AUTOMATION - INSTALADOR"
echo "========================================"
echo ""

# Colores
GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Verificar Python
echo "🔍 Verificando Python..."
if ! command -v python3 &> /dev/null; then
    echo -e "${RED}❌ Python3 no encontrado${NC}"
    exit 1
fi
echo -e "${GREEN}✓ Python $(python3 --version)${NC}"

# Instalar python3-venv si falta (requiere sudo)
echo ""
echo "🔍 Verificando python3-venv..."
if ! python3 -m venv --help &> /dev/null; then
    echo -e "${YELLOW}⚠ python3-venv no instalado${NC}"
    echo "Intentando instalar..."

    if command -v apt &> /dev/null; then
        echo "   Usando apt (necesita sudo)..."
        sudo apt update && sudo apt install -y python3-venv python3-pip
    elif command -v yum &> /dev/null; then
        echo "   Usando yum (necesita sudo)..."
        sudo yum install -y python3-venv python3-pip
    elif command -v brew &> /dev/null; then
        echo "   Usando brew..."
        brew install python3
    else
        echo -e "${RED}❌ No se pudo instalar python3-venv automáticamente${NC}"
        echo "   Instala manualmente: sudo apt install python3.12-venv"
        exit 1
    fi
else
    echo -e "${GREEN}✓ python3-venv disponible${NC}"
fi

# Crear entorno virtual
echo ""
echo "📦 Creando entorno virtual..."
if [ -d "venv" ]; then
    echo "   Eliminando venv existente..."
    rm -rf venv
fi

python3 -m venv venv
echo -e "${GREEN}✓ Entorno virtual creado${NC}"

# Activar venv
echo ""
echo "⚙️ Instalando dependencias..."
source venv/bin/activate

# Upgrade pip
pip install --upgrade pip setuptools wheel

# Instalar dependencias principales
echo "   Instalando paquetes de requirements.txt..."
pip install -r requirements.txt

# Dependencias opcionales
echo ""
read -p "¿Instalar dependencias opcionales? (redis, db, dev) [y/N]: " -n 1 -r
echo
if [[ $REPLY =~ ^[Yy]$ ]]; then
    echo "   Instalando dependencias extras..."
    pip install -e ".[dev,redis,db]"
fi

# Variables de entorno
echo ""
echo "📝 Configurando variables de entorno..."
if [ ! -f .env ]; then
    cp .env.example .env
    echo -e "${GREEN}✓ .env creado desde .env.example${NC}"
    echo ""
    echo -e "${YELLOW}⚠ IMPORTANTE:${NC}"
    echo "   Edita el archivo .env y configura:"
    echo "   - OPENAI_API_KEY (para transcripción de audio)"
    echo "   - N8N_BASE_URL (para ejecutar workflows)"
    echo "   - N8N_API_KEY (si tu instancia n8n requiere auth)"
    echo ""
    read -p "¿Quieres editar .env ahora? [y/N]: " -n 1 -r
    echo
    if [[ $REPLY =~ ^[Yy]$ ]]; then
        ${EDITOR:-nano} .env
    fi
else
    echo -e "${GREEN}✓ .env ya existe${NC}"
fi

# Validar configuración
echo ""
echo "🔍 Validando configuración..."
python3 validate_configs.py || {
    echo -e "${YELLOW}⚠ Algunas validaciones fallaron${NC}"
    echo "   Revisa los errores antes de continuar"
}

# Crear directorios necesarios
echo ""
echo "📁 Creando directorios..."
mkdir -p logs data
echo -e "${GREEN}✓ Directorios creados${NC}"

# Test de instalación
echo ""
echo "🧪 Ejecutando test de instalación..."
if python3 -c "from arcadium_automation import settings; print('✓ Import OK')" 2>/dev/null; then
    echo -e "${GREEN}✓ Importación exitosa${NC}"
else
    echo -e "${YELLOW}⚠ Importación falló (puede ser por variables de entorno)${NC}"
fi

echo ""
echo "========================================"
echo -e "${GREEN}✅ INSTALACIÓN COMPLETADA${NC}"
echo "========================================"
echo ""
echo "Próximos pasos:"
echo ""
echo "1. Configura .env si no lo hiciste:"
echo "   nano .env"
echo ""
echo "2. Valida la configuración:"
echo "   source venv/bin/activate"
echo "   python -m arcadium_automation validate"
echo ""
echo "3. Ejecuta el sistema:"
echo "   python -m arcadium_automation start"
echo ""
echo "4. O prueba con demo:"
echo "   python quickstart.py"
echo ""
echo "5. O procesa un webhook manualmente:"
echo "   python -m arcadium_automation process --file test_payload.json"
echo ""
echo "Documentación:"
echo "   - README: cat README.md"
echo "   - Makefile: make help"
echo ""
echo "¡Listo! 🚀"
echo ""
