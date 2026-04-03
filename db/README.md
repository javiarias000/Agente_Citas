# Base de Datos - Arcadium Automation

## 📋 Tablas

### 1. conversations

Cabecera de conversación (una por número de teléfono).

```sql
id UUID PRIMARY KEY
phone_number VARCHAR(20) NOT NULL
platform VARCHAR(50) DEFAULT 'whatsapp'
status VARCHAR(50) DEFAULT 'active'
metadata JSONB DEFAULT '{}'
created_at TIMESTAMPTZ DEFAULT NOW()
updated_at TIMESTAMPTZ DEFAULT NOW()
```

### 2. messages

Mensajes individuales de cada conversación.

```sql
id UUID PRIMARY KEY
conversation_id UUID REFERENCES conversations(id) ON DELETE CASCADE
direction VARCHAR(20) -- 'inbound' | 'outbound'
message_type VARCHAR(50) DEFAULT 'text'
content TEXT
raw_payload JSONB DEFAULT '{}'
processed BOOLEAN DEFAULT FALSE
processing_error TEXT
agent_response TEXT
tool_calls JSONB DEFAULT '[]'
execution_time_ms BIGINT
created_at TIMESTAMPTZ DEFAULT NOW()
```

### 3. appointments

Citas agendadas por `AppointmentService`.

```sql
id UUID PRIMARY KEY
phone_number VARCHAR(20) NOT NULL
appointment_date TIMESTAMPTZ NOT NULL
service_type VARCHAR(100) NOT NULL
status VARCHAR(50) -- 'scheduled' | 'cancelled' | 'completed' | 'no_show'
notes TEXT
metadata JSONB DEFAULT '{}'
created_at TIMESTAMPTZ DEFAULT NOW()
updated_at TIMESTAMPTZ DEFAULT NOW()
```

### 4. tool_call_logs

Audit trail de todas las tool calls del agente.

```sql
id UUID PRIMARY KEY
session_id VARCHAR(100) NOT NULL
tool_name VARCHAR(100) NOT NULL
input_data JSONB NOT NULL
output_data JSONB
success BOOLEAN DEFAULT TRUE
error_message TEXT
execution_time_ms BIGINT
created_at TIMESTAMPTZ DEFAULT NOW()
```

### 5. langchain_memory

Memoria de conversación para LangChain (tabla única, escalable).

```sql
id SERIAL PRIMARY KEY
session_id VARCHAR(255) NOT NULL
type VARCHAR(20) CHECK (type IN ('human', 'ai'))
content TEXT NOT NULL
created_at TIMESTAMPTZ DEFAULT NOW()
```

**Índices:**

- `idx_langchain_memory_session_created` (session_id, created_at)
- `idx_langchain_memory_created_at` (created_at DESC)

---

## 🚀 Migraciones

### Opción 1: Script rápido (crea todas las tablas)

```bash
python db/create_schema_simple.py
```

### Opción 2: Sistema de migraciones (recomendado)

```bash
# Ver migraciones pendientes
./run.sh verify

# Aplicar todas las migraciones
./run.sh migrate

# Resetear (¡PELIGROSO! - borra todo)
./run.sh db-reset
```

### Opción 3: Automático (al iniciar la app)

Las migraciones se ejecutan automáticamente cuando inicias la API:

```bash
./run.sh dev
# En logs verás: "Migrations completed successfully"
```

---

## 🔍 Verificación

```bash
# Verificar esquema completo
python db/verify.py

# Output esperado:
# ✅ Connection
# ✅ Tables (6 tablas)
# ✅ Migrations
# ✅ Indexes
# ✅ Constraints
```

---

## 📊 Vistas Útiles

Las migraciones crean estas vistas:

### `conversation_stats`

Estadísticas por conversación:

```sql
SELECT * FROM conversation_stats WHERE phone_number='+1234567890';
```

### `appointment_stats`

Estadísticas de citas por cliente:

```sql
SELECT * FROM appointment_stats WHERE phone_number='+1234567890';
```

### `recent_activity`

Actividad reciente (mensajes + citas):

```sql
SELECT * FROM recent_activity LIMIT 20;
```

---

## 🔧 Funciones Útiles

### `get_conversation_history(phone_number, limit)`

Obtiene historial de mensajes de un teléfono:

```python
from db.connection import get_connection

conn = get_connection()
with conn.cursor() as cur:
    cur.callproc('get_conversation_history', ['+1234567890', 50])
    for row in cur.fetchall():
        print(row)
```

### `cleanup_old_memories(days_old)`

Elimina memorias antiguas:

```python
from db.connection import get_connection

conn = get_connection()
with conn.cursor() as cur:
    cur.execute("SELECT cleanup_old_memories(30);")
    deleted = cur.fetchone()[0]
    print(f"Deleted {deleted} old memory records")
```

---

## ⚙️ Configuración Supabase

Si usas Supabase, conecta a tu base de datos:

1. En Supabase Dashboard → Project Settings → Database
2. Copia la **Connection string** (format: `postgresql://...`)
3. Pégala en `.env`:

```bash
DATABASE_URL=postgresql://postgres.[PROJECT_REF]:[PASSWORD]@aws-...pooler.supabase.com:5432/postgres
```

4. Ejecuta migraciones:

```bash
./run.sh migrate
```

O ejecuta manualmente:

```bash
psql "postgresql://..." -f db/migrations/001_initial_schema.sql
```

---

## 🧹 Limpieza

### Manual (SQL directo)

```sql
-- Borrar todas las tablas (PELIGRO!)
DROP SCHEMA public CASCADE;
CREATE SCHEMA public;
GRANT ALL ON SCHEMA public TO public;
```

### Automático

```bash
./run.sh db-reset
```

---

## 🐛 Troubleshooting

### Error: "pgcrypto extension not found"

```sql
CREATE EXTENSION IF NOT EXISTS "pgcrypto";
```

### Error: "permission denied for schema public"

```sql
GRANT ALL ON SCHEMA public TO tu_usuario;
```

### Error: "relation does not exist"

Las tablas no se han creado. Ejecuta:

```bash
python db/create_schema_simple.py
```

### Tabla `langchain_memory` no existe

El agente la crea automáticamente al usarla, pero puedes crearla manualmente:

```bash
./run.sh migrate
```

---

## 📈 Escalabilidad

### Partitioning (para >1M de registros)

`langchain_memory` puede particionarse por mes:

```sql
-- Habilitar particionamiento
ALTER TABLE langchain_memory
    PARTITION BY RANGE (created_at);

-- Crear particiones mensuales
CREATE TABLE langchain_memory_2025_04 PARTITION OF langchain_memory
    FOR VALUES FROM ('2025-04-01') TO ('2025-05-01');
```

### Indexes adicionales

Para queries específicas:

```sql
-- En messages: búsqueda por contenido
CREATE INDEX idx_messages_content_gin ON messages USING GIN(to_tsvector('english', content));

-- En appointments: búsqueda por rango de fechas
CREATE INDEX idx_appointments_date_range ON appointments(appointment_date)
WHERE status = 'scheduled';
```

---

## 🔄 Rollback

No hay rollback automático. Para deshacer una migración:

1. Identificar Statement problemático
2. Ejecutar manualmente DROP TABLE / DROP INDEX
3. O usar `db-reset` para empezar de cero (⚠️ borra todo)

---

## 📝 Best Practices

1. ✅ **SIEMPRE** usar migrations, nunca `CREATE TABLE` manual en producción
2. ✅ Usar `db/migrate.py` para entornos de staging/producción
3. ✅ Hacer backup antes de `db-reset`
4. ✅ Monitorear tamaño de tablas (especialmente `langchain_memory`)
5. ✅ Implementar TTL/partitioning si `langchain_memory` crece >1M

---

## 🆘 Soporte

- **Errores de conexión**: Verificar `DATABASE_URL` en `.env`
- **Tablas faltantes**: Ejecutar `./run.sh migrate`
- **Performance**: Revisar índices en `db/migrations/001_initial_schema.sql`
