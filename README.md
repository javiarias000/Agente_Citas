# 🤖 Agente_Citas - Sistema de Gestión de Citas con IA

Sistema automatizado para gestión de citas mediante WhatsApp con integración de Google Calendar y LangChain.

## 📋 Características

- ✅ **Agente IA (DeyyAgent)**: Basado en LangChain con herramientas personalizadas
- ✅ **WhatsApp Automation**: Integración directa con Evolution API
- ✅ **Google Calendar Sync**: Sincronización bidireccional de citas
- ✅ **Base de datos PostgreSQL**: Persistencia completa de conversaciones y citas
- ✅ **WebSocket Chat**: Interface de chat en tiempo real
- ✅ **100% Async/Await**: Alto rendimiento con asyncio
- ✅ **Landchain Architecture**: Procesamiento secuencial con validación
- ✅ **Métricas Prometheus**: Monitoreo integrado

## 🏗️ Arquitectura

```
WhatsApp → FastAPI Webhook → DeyyAgent → PostgreSQL + Google Calendar → WhatsApp Response
```

### Componentes principales:

| Componente             | Ubicación                             | Descripción                                        |
| ---------------------- | ------------------------------------- | -------------------------------------------------- |
| **Orchestrator**       | `core/orchestrator.py`                | API FastAPI principal, maneja webhooks y WebSocket |
| **DeyyAgent**          | `agents/deyy_agent.py`                | Agente LangChain con herramientas para citas       |
| **Landchain**          | `core/landchain.py`                   | Sistema de cadenas de procesamiento                |
| **Google Calendar**    | `services/google_calendar_service.py` | Integración con Google Calendar API                |
| **AppointmentService** | `services/appointment_service.py`     | Lógica de negocio para citas                       |
| **Memory Manager**     | `memory/memory_manager.py`            | Gestión de memoria (PostgreSQL/InMemory)           |

## 🛠️ Herramientas del Agente

El agente Deyy puede:

1. **consultar_disponibilidad(fecha, servicio_opcional)**
   - Consulta slots disponibles en Google Calendar
   - Considera duración del servicio y horario laboral (9:00-18:00, Lun-Vie)

2. **agendar_cita(fecha, servicio, notas_opcional)**
   - Crea evento en Google Calendar
   - Guarda registro en PostgreSQL
   - Valida fecha y disponibilidad

3. **obtener_citas_cliente(historico_opcional)**
   - Muestra citas agendadas del cliente
   - Por defecto muestra próximas citas

4. **cancelar_cita(appointment_id_opcional)**
   - Cancela una cita específica o la próxima del cliente
   - Elimina de Google Calendar y actualiza DB

5. **reagendar_cita(fecha_nueva, appointment_id_opcional)**
   - Reprograma una cita existente

## 🚀 Inicio Rápido

### Prerrequisitos

- Python 3.12+
- PostgreSQL
- Google Cloud Console (para Calendar API)
- Evolution API (para WhatsApp)

### Instalación

```bash
# 1. Clonar repositorio
git clone https://github.com/javiarias000/Agente_Citas.git
cd Agente_Citas

# 2. Crear entorno virtual
python -m venv venv
source venv/bin/activate  # Linux/Mac
# o
venv\Scripts\activate  # Windows

# 3. Instalar dependencias
pip install -r requirements.txt

# 4. Configurar variables de entorno
cp .env.example .env
# Editar .env con tus configuración
```

### Variables de Entorno (.env)

```bash
# OpenAI
OPENAI_API_KEY=sk-...

# Database
DATABASE_URL=postgresql+asyncpg://user:pass@localhost:5432/arcadium

# WhatsApp (Evolution API)
WHATSAPP_API_URL=https://tu-evolution-api.com
WHATSAPP_API_TOKEN=tu_token
WHATSAPP_INSTANCE_NAME=tu_instancia

# Google Calendar (OAuth2)
GOOGLE_CALENDAR_ENABLED=true
GOOGLE_CALENDAR_CREDENTIALS_PATH=./credentials/google_credentials.json
GOOGLE_CALENDAR_DEFAULT_ID=tu-email@gmail.com
GOOGLE_CALENDAR_TIMEZONE=America/Guayaquil
GOOGLE_REDIRECT_URI=http://localhost:8000/oauth2callback

# Opcional
OPENAI_MODEL=gpt-4o-mini
OPENAI_TEMPERATURE=0.7
DEBUG=false
```

### Configurar Google Calendar

1. Ve a [Google Cloud Console](https://console.cloud.google.com/)
2. Crear proyecto o seleccionar existente
3. Habilitar **Google Calendar API**
4. Crear **OAuth 2.0 Client ID** ( tipo "Web application" )
5. Agregar redirect URI: `http://localhost:8000/oauth2callback`
6. Descargar credenciales JSON a `credentials/google_credentials.json`

### Inicializar Base de Datos

```bash
# Crear tablas automáticamente al iniciar
./run.sh start

# O manual:
python db/create_schema_simple.py
```

### Obtener Token de Google Calendar (una sola vez)

```bash
# 1. Ir al endpoint de auth ( abre navegador )
curl http://localhost:8000/auth/google

# 2. Autorizar en Google
# 3. Te redirige a /oauth2callback y guarda token.json automáticamente
```

### Iniciar Servidor

```bash
./run.sh start
```

El servidor estará en: **http://localhost:8000**

## 📡 Endpoints

| Endpoint                    | Método    | Descripción                               |
| --------------------------- | --------- | ----------------------------------------- |
| `/`                         | GET       | Información de la API                     |
| `/health`                   | GET       | Health check                              |
| `/metrics`                  | GET       | Métricas Prometheus (si habilitado)       |
| `/chat`                     | GET       | Interface de chat WebSocket               |
| `/ws/{session_id}`          | WebSocket | Chat en tiempo real                       |
| `/api/history/{session_id}` | GET       | Historial de conversación                 |
| `/auth/google`              | GET       | Iniciar OAuth Google Calendar (admin)     |
| `/oauth2callback`           | GET       | Callback OAuth (procesamiento automático) |
| `/api/calendar/status`      | GET       | Estado de conexión Google Calendar        |

## 🧪 Testing

```bash
# Tests unitarios
./run.sh test

# Con coverage
./run.sh test --coverage

# Endpoint de prueba (sin enviar a WhatsApp)
curl -X POST http://localhost:8000/webhook/test \
  -H "Content-Type: application/json" \
  -d '{"message": "Hola, quiero agendar una cita"}'
```

## 🐛 Debugging

### Ver logs en tiempo real

```bash
./run.sh logs
# o
tail -f logs/arcadium_automation.log
```

### Modo debug

Establecer `DEBUG=true` en `.env` para:

- Endpoint `/debug/agent/{session_id}` con estado interno del agente
- Logs más detallados

### Shell interactivo

```bash
./run.sh shell
# Abre Python con orchestrator pre-inicializado
```

## 📊 Monitoreo

- **Health check**: `GET /health`
- **Métricas**: `GET /metrics` (Prometheus)
- **Estado agentes**: `GET /debug/agent/{session_id}` (solo DEBUG=true)

### Métricas incluidas

- `arcadium_chains_executed_total`
- `arcadium_chains_duration_seconds`
- `arcadium_links_executed_total`
- `arcadium_active_agents`

## 🔧 Desarrollo

### Estructura de archivos

```
arcadium_automation/
├── agents/              # Agentes LangChain
│   └── deyy_agent.py   # Agente principal
├── chains/              # Landchain processing
├── config/              # Configuración adicional
├── core/                # Núcleo del sistema
│   ├── config.py       # Settings
│   ├── landchain.py    # LandChain
│   ├── orchestrator.py # FastAPI app
│   └── state.py        # StateManager
├── db/                  # Base de datos
│   ├── models.py       # SQLAlchemy models
│   └── migrate.py      # Migraciones
├── memory/              # Backends de memoria
├── services/            # Servicios externos
│   ├── google_calendar_service.py
│   └── appointment_service.py
├── static/              # Frontend assets
│   └── chat.js
├── templates/           # HTML templates
│   └── chat.html
├── credentials/         # Credenciales OAuth (NO subir a git)
├── logs/               # Logs de ejecución
├── data/               # Datos SQLite (si se usa)
├── run.sh              # Script principal
├── Makefile            # Comandos comunes
└── requirements.txt    # Dependencias
```

### Comandos útiles

```bash
make install      # Instalar dependencias
make test         # Ejecutar tests
make lint         # Verificar estilo
make format       # Formatear código
make run          # Iniciar servidor
make validate     # Validar configuración
make check        # Health check rápido
```

## 📚 Documentación

- `ARCHITECTURE.md` - Arquitectura detallada (español)
- `COMPLETE_GUIDE.md` - Guía completa de uso
- `INSTRUCCIONES_INSTALACION.md` - Instalación paso a paso
- `GOOGLE_CALENDAR_SETUP.md` - Configuración Google Calendar

## 🤝 Contribuir

1. Fork el proyecto
2. Crear rama feature (`git checkout -b feature/nueva-funcionalidad`)
3. Commit cambios (`git commit -m 'Add nueva funcionalidad'`)
4. Push a la rama (`git push origin feature/nueva-funcionalidad`)
5. Abrir Pull Request

## 📄 Licencia

[Tu licencia aquí]

---

**Desarrollado con ❤️ por el equipo de Arcadium**
