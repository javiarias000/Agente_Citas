# 🎯 Guía de Inicio Rápido - Arcadium Automation

## ⚡ 5 minutos para empezar

### 1. Instalar (1 min)

```bash
cd /home/jav/arcadium_automation

# Si es primera vez, instala dependencias del sistema:
sudo apt update && sudo apt install -y python3.12-venv python3-pip

# Ejecuta el instalador automático:
bash install.sh
```

### 2. Configurar (2 min)

```bash
# Edita el archivo .env (el instalador ya lo creó)
nano .env
```

Configura al menos:

```
N8N_BASE_URL=http://localhost:5678  # URL de tu n8n
OPENAI_API_KEY=sk-...               # Si transcibes audio
```

### 3. Probar (1 min)

```bash
# Activa el entorno virtual
source venv/bin/activate

# Ejecuta la demo
python quickstart.py
```

### 4. Ejecutar (1 min)

```bash
# Opción A: Usando run.sh (recomendado)
./run.sh start

# Opción B: Python directo
python -m arcadium_automation start
```

---

## 📋 Comandos Esenciales

| Comando                                     | Descripción              |
| ------------------------------------------- | ------------------------ |
| `./run.sh start`                            | Iniciar orquestador      |
| `./run.sh status`                           | Ver estado del sistema   |
| `./run.sh metrics`                          | Monitoreo en tiempo real |
| `./run.sh logs`                             | Ver logs en vivo         |
| `./run.sh test`                             | Ejecutar tests           |
| `./run.sh demo`                             | Demostración             |
| `./run.sh process --file test_payload.json` | Procesar webhook         |

---

## 📁 Estructura del Instituto

```
/home/jav/arcadium_automation/
├── venv/                    # Entorno virtual Python
├── core/                    # Núcleo del sistema
│   ├── config.py           # Configuración
│   ├── landchain.py        # Sistema de cadenas
│   ├── orchestrator.py     # Orquestador
│   └── state.py            # Gestión de estado
├── chains/                  # Cadenas específicas
├── utils/                   # Utilidades (n8n, transcriber, monitor)
├── validators/              # Validadores Pydantic
├── tests/                   # Suite de tests
├── logs/                    # Logs de ejecución
├── data/                    # Datos persistentes
├── config/                  # Configs adicionales
├── run.sh                   # ⭐ Script principal de ejecución
├── install.sh               # ⭐ Instalador automático
├── quickstart.py            # ⭐ Demo interactiva
├── validate_configs.py      # Validador de configs
├── workflow_example.json    # Ejemplo de workflow n8n
├── test_payload.json        # Payload de prueba
├── requirements.txt         # Dependencias Python
├── docker-compose.yml       # Opcional: todo en Docker
└── README.md                # Documentación completa
```

---

## 🔧 Configuración .env

```bash
# ========== REQUERIDO ==========
N8N_BASE_URL=http://localhost:5678

# ========== OPCIONAL ==========
OPENAI_API_KEY=sk-...          # Para audio
CHATWOOT_API_URL=...           # Para respuestas
STATE_STORAGE=memory          # memory|redis|sqlite
STRICT_VALIDATION=true        #
ENABLE_METRICS=true           # Prometheus en :9090
LOG_LEVEL=INFO                # DEBUG|INFO|WARNING|ERROR
```

---

## 🧪 Testing

```bash
# Test completo con coverage
./run.sh test --coverage

# Tests específicos
pytest tests/test_landchain.py -v

# Validar configuración
./run.sh validate
```

---

## 📊 Monitoreo

- **Métricas Prometheus**: http://localhost:9090/metrics (si ENABLE_METRICS=true)
- **Logs**: `tail -f logs/arcadium_automation.log`
- **Estado**: `./run.sh status`
- **Consola interactiva**: `./run.sh shell`

---

## 🐛 Troubleshooting

| Problema                 | Solución                                                      |
| ------------------------ | ------------------------------------------------------------- |
| `python3-venv not found` | `sudo apt install python3.12-venv`                            |
| Import errors            | `source venv/bin/activate && pip install -r requirements.txt` |
| n8n connection failed    | Verifica N8N_BASE_URL en .env                                 |
| Whisper API error        | Configura OPENAI_API_KEY                                      |
| Port 9090 in use         | Cambia METRICS_PORT en .env                                   |

---

## 📚 Más Información

- **README.md**: Documentación completa
- **Archivo de ejemplo**: `workflow_example.json`
- **Test payload**: `test_payload.json`
- **Makefile**: `make help` para más comandos

---

## 🆘 Soporte

1. Revisa logs: `./run.sh logs`
2. Valida config: `./run.sh validate`
3. Ejecuta tests: `./run.sh test`
4. Consulta README.md

---

**🚀 Sistema 100% Efectivo con Landchain Architecture**
