# Test de Interfaz de Chat Web

## Pruebas Manuales

### 1. Iniciar el Servidor

```bash
source venv/bin/activate
./run.sh start
# o
python -m arcadium_automation start
```

### 2. Acceder a la Interfaz de Chat

Abrir navegador en: `http://localhost:8000/chat`

### 3. Probar Funcionalidades

#### Chat Básico

- Enviar mensaje de texto: "Hola"
- Recibir respuesta del agente Deyy

#### Consulta de Herramientas

- "¿Qué citas tengo?" → Usa `obtener_citas_cliente`
- "Quiero agendar una cita" → Usa `agendar_cita`
- "¿Hay disponibilidad el 2025-12-25?" → Usa `consultar_disponibilidad`

#### Historial

- Recargar la página → Debería cargar el historial de la sesión desde `/api/history/{session_id}`
- Los mensajes previos deberían aparecer

#### Indicadores

- Indicador de conexión (verde/rojo) en la esquina superior derecha
- Indicador de "escribiendo..." mientras el agente procesa
- Herramientas usadas mostradas debajo de los mensajes del asistente

#### Reconexión WebSocket

- Detener el servidor → El cliente debería intentar reconectar cada 3 segundos
- Reiniciar el servidor → El cliente se reconecta automáticamente

### 4. Verificar Logs

```bash
./run.sh logs
# Buscar eventos WebSocket:
# - "WebSocket conectado"
# - "Mensaje recibido via WS"
# - "Mensaje procesado"
```

## API Endpoints

### GET /chat

- Sirve la página HTML del chat
- Debería devolver HTML con la interfaz

### GET /api/history/{session_id}

- Devuelve historial de mensajes LangChain
- Ejemplo respuesta:

```json
{
  "session_id": "session_abc123",
  "messages": [
    { "type": "HumanMessage", "content": "Hola" },
    { "type": "AIMessage", "content": "¡Hola! ¿En qué puedo ayudarte?" }
  ],
  "count": 2
}
```

### WebSocket /ws/{session_id}

- Conexión WebSocket para chat en tiempo real
- Enviar: `{"message": "texto del mensaje"}`
- Recibir:
  - `{"type": "response", "content": "...", "tool_calls": [...], "execution_time": 1.23}`
  - `{"type": "tools_used", "tools": [...]}`
  - `{"type": "error", "message": "..."}`

## Verificaciones de Calidad

- [x] Interfaz responsive (funciona en móvil y desktop)
- [x] Sin errores de JavaScript en consola del navegador
- [x] WebSocket se conecta/desconecta correctamente
- [x] Historial persiste entre recargas
- [x] Herramientas se muestran correctamente
- [x] Tiempos de ejecución mostrados
- [x] Manejo de errores (mensajes de error visibles)
- [x] Reconexión automática

## Debug

### Ver estado de agentes

```bash
curl http://localhost:8000/debug/agent/{session_id}
# (solo si DEBUG=true en .env)
```

### Ver logs detallados

```bash
# En .env: LOG_LEVEL=DEBUG
./run.sh logs
```
