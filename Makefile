Makefile para Arcadium Automation
"""

.PHONY: help install test clean run demo lint

help:
	@echo "Arcadium Automation - Comandos disponibles"
	@echo ""
	@echo "  install     Instalar dependencias"
	@echo "  test        Ejecutar tests"
	@echo "  test-cov    Tests con coverage"
	@echo "  lint        Linting (black + flake8)"
	@echo "  run         Iniciar orquestador"
	@echo "  demo        Ejecutar demostración"
	@echo "  clean       Limpiar artefactos"
	@echo "  validate    Validar configuración"
	@echo "  check       Health check del sistema"
	@echo "  metrics     Ver métricas (console)"
	@echo "  status      Estado del sistema"

install:
	pip install -r requirements.txt
	@echo "✅ Instalación completada"

test:
	pytest tests/ -v --tb=short

test-cov:
	pytest tests/ --cov=. --cov-report=html --cov-report=term
	@echo "📊 Reporte HTML en htmlcov/index.html"

lint:
	black --check arcadium_automation/ tests/
	flake8 arcadium_automation/ tests/ --max-line-length=100

format:
	black arcadium_automation/ tests/

run:
	python -m arcadium_automation start

demo:
	python quickstart.py

clean:
	rm -rf __pycache__ .pytest_cache htmlcov coverage.xml .coverage
	rm -rf arcadium_automation/__pycache__ tests/__pycache__
	find . -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
	@echo "🧹 Limpieza completada"

validate:
	python -m arcadium_automation validate

check:
	@echo "🔍 Verificando estado del sistema..."
	@pkill -f "arcadium_automation" 2>/dev/null || true
	timeout 5s python -c "
import asyncio
from core.orchestrator import ArcadiumAutomation
async def check():
    orch = ArcadiumAutomation()
    await orch.initialize()
    status = await orch.get_health_status()
    print(f\"\\n🏥 Estado: {status.get('status')}\\n\")
    await orch.shutdown()
asyncio.run(check())
" 2>/dev/null || echo "❌ Verifica que las variables de entorno estén configuradas"

metrics:
	@echo "📊 Métricas disponibles en http://localhost:9090/metrics (si están habilitadas)"
	@echo "Para ver en consola: python -m arcadium_automation metrics"

logs:
	@echo "📄 Últimas líneas de logs:"
	@tail -f logs/arcadium_automation.log 2>/dev/null || echo "No hay logs aún. Ejecuta 'make run' primero."

shell:
	python -i -c "from core.orchestrator import ArcadiumAutomation; orch = ArcadiumAutomation(); import asyncio; asyncio.run(orch.initialize()); print(orch.__dict__)"

.DEFAULT_GOAL := help
