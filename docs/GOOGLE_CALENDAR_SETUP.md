# 🔧 Configuración de Google Calendar - Arcadium Automation

## 📋 Índice

1. [Requisitos](#requisitos)
2. [Configurar Google Cloud](#configurar-google-cloud)
3. [Obtener Credenciales OAuth2](#obtener-credenciales-oauth2)
4. [Configurar Arcadium](#configurar-arcadium)
5. [Probar la Integración](#probar-la-integración)
6. [Troubleshooting](#troubleshooting)

---

## 📦 Requisitos

- ✅ Dependencias instaladas:
  ```bash
  pip install google-api-python-client google-auth-httplib2 google-auth-oauthlib google-auth
  ```
- ✅ Cuenta de Google con Calendar activado
- ✅ ID de Calendarios configurados (ya los tienes):
  - Cirugía: `javiarias000@gmail.com`
  - Ortodoncia: `jorge.arias.amauta@gmail.com`

---

## 🚀 Configurar Google Cloud

### Paso 1: Crear Proyecto en Google Cloud Console

1. Ve a [Google Cloud Console](https://console.cloud.google.com/)
2. Crea nuevo proyecto o selecciona existente
3. Habilita **Google Calendar API**:
   - APIs & Services → Library
   - Busca "Google Calendar API"
   - Click "Enable"

### Paso 2: Crear Credenciales OAuth2

1. Ve a **APIs & Services → Credentials**
2. Click **"Create Credentials" → "OAuth 2.0 Client ID"**
3. Application type: **Desktop app**
4. Name: `Arcadium Calendar`
5. Click **Create**
6. Click **Download JSON**
7. Renombra el archivo a `google_credentials.json`
8. Mueve el archivo a:
   ```
   /home/jav/arcadium_automation/credentials/google_credentials.json
   ```

### Paso 3: Compartir Calendarios

**IMPORTANTE:** La cuenta de Google que usa la app (las credenciales) debe tener acceso a los calendarios de los doctores.

1. Inicia sesión en Google Calendar con esa cuenta
2. En Settings → Share with specific people
3. Agrega los emails de los doctores:
   - `javiarias000@gmail.com`
   - `jorge.arias.amauta@gmail.com`
4. Permisos: **"Make changes to events"** (para crear/editar/eliminar)

---

## ⚙️ Configurar Arcadium

### 1. Variables `.env`

Ya están configuradas, pero verifica:

```bash
GOOGLE_CALENDAR_ENABLED=true
GOOGLE_CALENDAR_CREDENTIALS_PATH=./credentials/google_credentials.json
GOOGLE_CALENDAR_DEFAULT_ID=jorge.arias.amauta@gmail.com
GOOGLE_CALENDAR_TIMEZONE=America/Guayaquil
```

**Nota:** `GOOGLE_CALENDAR_DEFAULT_ID` se usa como fallback si un servicio no tiene odontólogo asignado. Lo usas para ortodoncia (que también atiende general).

### 2. Mapeo de Servicios → Odontólogos

Ya está configurado en `config/calendar_mapping.py`:

| Servicio          | Odontólogo  | Calendar ID                    | Duración  |
| ----------------- | ----------- | ------------------------------ | --------- |
| Consulta inicial  | Jorge Arias | `jorge.arias.amauta@gmail.com` | 30 min    |
| Limpieza dental   | Jorge Arias | `jorge.arias.amauta@gmail.com` | 45 min    |
| Empaste           | Jorge Arias | `jorge.arias.amauta@gmail.com` | 45 min    |
| Endodoncia        | Jorge Arias | `jorge.arias.amauta@gmail.com` | 60-90 min |
| Ortodoncia        | Jorge Arias | `jorge.arias.amauta@gmail.com` | 60 min    |
| Extracción simple | Javi Arias  | `javiarias000@gmail.com`       | 45 min    |
| Cirugía oral      | Javi Arias  | `javiarias000@gmail.com`       | 60-90 min |
| Implantes         | Javi Arias  | `javiarias000@gmail.com`       | 90 min    |
| ...               | ...         | ...                            | ...       |

**Para cambiar asignación**, edita `config/calendar_mapping.py` → `SERVICE_TO_DENTIST`.

### 3. Primera Autenticación

La primera vez que uses una tool que requiera Google Calendar, se abrirá un navegador para autorizar.

**IMPORTANTE:** Si el servidor corre headless (sin GUI), usa **OAuth2 con service account** o genera un refresh token manualmente y agrega a `.env`:

```bash
# Generar refresh token (una sola vez, en tu PC):
python -c "
from google_auth_oauthlib.flow import InstalledAppFlow

flow = InstalledAppFlow.from_client_secrets_file(
    'credentials/google_credentials.json',
    ['https://www.googleapis.com/auth/calendar']
)

creds = flow.run_local_server(port=8080, open_browser=True)
print('Refresh Token:', creds.refresh_token)
"
```

Luego agrega a `.env`:

```env
GOOGLE_REFRESH_TOKEN=tu_refresh_token_aqui
```

---

## 🧪 Probar la Integración

### Opción A: Via Web Chat (Recomendado)

1. Inicia el servidor:

   ```bash
   source venv/bin/activate
   ./run.sh start
   ```

2. Abre navegador: `http://localhost:8000/chat`

3. Prueba flujo completo:

   ```
   Tú: Hola, quiero agendar una limpieza dental
   Deyy: ¿Para qué fecha y hora?
   Tú: El 25 de diciembre a las 14:00
   Deyy: Consultando disponibilidad...
        ✅ Disponible a las 14:00 (45 min) con Dr. Jorge Arias.
        ¿Confirmas agendar?
   Tú: Sí
   Deyy: ✅ Cita agendada para 25/12/2025 14:00
        📅 Link: https://calendar.google.com/...
   ```

4. Verifica en Google Calendar del doctor que apareció el evento.

### Opción B: Via API Test

```bash
# 1. Consultar disponibilidad
curl -X POST http://localhost:8000/webhook/test \
  -H "Content-Type: application/json" \
  -d '{"session_id": "test123", "message": "¿Hay disponibilidad el 2025-12-25?"}'

# 2. Agendar cita
curl -X POST http://localhost:8000/webhook/test \
  -H "Content-Type: application/json" \
  -d '{
    "session_id": "test123",
    "message": "Agendar limpieza para 2025-12-25T14:00"
  }'

# 3. Ver mis citas
curl -X POST http://localhost:8000/webhook/test \
  -H "Content-Type: application/json" \
  -d '{"session_id": "test123", "message": "¿Qué citas tengo?"}'

# 4. Cancelar cita (usa el ID devuelto)
curl -X POST http://localhost:8000/webhook/test \
  -H "Content-Type: application/json" \
  -d '{"session_id": "test123", "message": "Cancelar cita"}'
```

### Opción C: Ver logs en tiempo real

```bash
./run.sh logs
# Buscar eventos:
# - "GoogleCalendarService inicializado"
# - "Evento creado en Google Calendar"
# - "Cita creada y sincronizada"
```

---

## 🔍 Casos de Prueba Específicos

### Test 1: Validación de Fecha Pasada

```
Tú: Quiero agendar cita para ayer
Deyy: ❌ No puedes agendar en el pasado.
```

### Test 2: Fin de Semana

```
Tú: ¿Hay slots el sábado 28/12?
Deyy: ❌ Las citas solo se agendan de lunes a viernes.
```

### Test 3: Horario No Laboral

```
Tú: Quiero a las 20:00
Deyy: ❌ Horario no laboral (9:00-18:00).
```

### Test 4: Consulta Disponibilidad con Servicio Específico

```
Tú: ¿Hay disponibilidad para una extracción el 26/12?
Deyy: Consulta slots de 45 min (duración extracción)
```

### Test 5: Agendar con Confirmación

```
Tú: Agenda limpieza 26/12 09:00
Deyy: ✅ Disponible 09:00-09:45 con Dr. Jorge
      ¿Confirmas?
Tú: Sí
Deyy: ✅ Agendado + link
```

### Test 6: Reagendar

```
Tú: Quiero cambiar mi cita del viernes
Deyy: Tu cita: 24/12 10:00 Consulta
      ¿A qué nueva fecha?
Tú: El lunes a la misma hora
Deyy: Consultando disponibilidad...
      ✅ Confirmar cambio a 29/12 10:00?
Tú: Sí
Deyy: ✅ Cita reagendada
```

### Test 7: Cancelar

```
Tú: Cancelar mi cita
Deyy: Cancelar: 24/12 10:00 Consulta? (sí/no)
Tú: Sí
Deyy: ✅ Cita cancelada + evento Google eliminado
```

---

## 🐛 Troubleshooting

### Error: "No module named 'google'"

```bash
pip install google-api-python-client google-auth-httplib2 google-auth-oauthlib
```

### Error: "Archivo de credenciales no encontrado"

- Asegúrate que existe `credentials/google_credentials.json`
- Ruta debe coincidir con `GOOGLE_CALENDAR_CREDENTIALS_PATH` en `.env`

### Error: "Invalid Grant" (OAuth)

- El token expiró → Borra `credentials/token.json` y reinicia
- Verifica que la cuenta tenga acceso a los calendars

### Error: "Address already in use" (9091)

```bash
# Matar proceso anterior
lsof -ti:9091 | xargs kill -9
# o cambiar puerto en .env: METRICS_PORT=9092
```

### Google Calendar no recibe eventos

- Verifica que la cuenta de credenciales tenga permiso **"Make changes"** en los calendars de los doctores
- Revisa logs: `./run.sh logs | grep -i google`
- Prueba con `curl` directo a Google API usando mismo token

### Eventos no se eliminan

- Verifica `google_event_id` en DB: `SELECT google_event_id FROM appointments WHERE id='...'`
- Si es null → no se sincronizó al crear (revisar logs)
- Si existe → verificar permisos de eliminación en Calendar

### El agente no usa Google Calendar

- Verifica `.env`: `GOOGLE_CALENDAR_ENABLED=true`
- Si hay error al crear `GoogleCalendarService`, el sistema cae a DB-only
- Revisa logs de inicio: buscar "GoogleCalendarService"

---

## 📊 Monitoreo

### Endpoints

- `GET /health` → Estado general
- `GET /metrics` → Métricas Prometheus (puerto 9091)
- `GET /debug/agent/{session_id}` → Debug (solo if DEBUG=true)

### Logs clave

```bash
# Buscar eventos de Google
grep -i "google\|calendar" logs/arcadium_automation.log

# Ver sincronización
grep "Cita creada" logs/arcadium_automation.log
grep "Evento creado en Google Calendar" logs/arcadium_automation.log
```

### Query DB para ver sincronización

```sql
SELECT
    id,
    phone_number,
    appointment_date,
    service_type,
    google_event_id,
    sync_status
FROM appointments
ORDER BY created_at DESC
LIMIT 10;
```

---

## 🎯 Checklist Post-Implementación

- [ ] Dependencias instaladas (`pip install -r requirements.txt`)
- [ ] Credenciales OAuth2 en `credentials/google_credentials.json`
- [ ] Calendarios compartidos con cuenta de app
- [ ] Variables `.env` configuradas (Google Calendar)
- [ ] Migración aplicada (`002_add_google_calendar_fields.sql`)
- [ ] Servidor iniciado sin errores
- [ ] Web chat funcionando (`/chat`)
- [ ] Test: Agendar cita → Verifica evento en Google Calendar
- [ ] Test: Cancelar cita → Verifica eliminación en Google Calendar
- [ ] Test: Consultar disponibilidad → Muestra slots correctos

---

## 📚 Referencias

- [Google Calendar API Python Quickstart](https://developers.google.com/calendar/api/quickstart/python)
- [OAuth 2.0 for Desktop Apps](https://developers.google.com/identity/protocols/oauth2/native-app)
- [LangChain Tools Documentation](https://python.langchain.com/docs/modules/agents/tools/)
- [Arcadium CLAUDE.md](./CLAUDE.md) (arquitectura)

---

**¡Listo!** El sistema debería estar completamente integrado con Google Calendar. Si surge algún problema, revisa logs y verifica que las credenciales y permisos estén correctos.
