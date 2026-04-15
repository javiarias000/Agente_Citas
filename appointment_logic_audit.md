# Audit de Lógica de Agendamiento - Arcadium Automation
Fecha: 2026-04-12

## Resumen Ejecutivo
El sistema utiliza una arquitectura basada en LangGraph con un enfoque determinista para operaciones críticas. Si bien la estructura es sólida, existen riesgos importantes de **atomicidad**, **consistencia de datos** y **condiciones de carrera** que podrían afectar la experiencia del paciente y la integridad de la agenda.

---

## 🚩 Bugs Críticos y Riesgos Altos

### 1. Falta de Atomicidad en Reagendamiento (SVR: ALTA)
- **Ubicación**: `src/nodes.py:1112-1148`
- **Problema**: El flujo de reagendamiento sigue el patrón `Borrar Anterior` $\rightarrow$ `Crear Nuevo`. Si la creación del nuevo evento falla después de borrar el anterior, el paciente pierde su cita por completo sin que se cree una nueva.
- **Comportamiento Actual**: Borra evento en Calendar $\rightarrow$ Borra en DB $\rightarrow$ Intenta crear nuevo.
- **Solución Sugerida**: Cambiar el orden a `Crear Nuevo` $\rightarrow$ `Borrar Anterior`. Solo borrar la cita vieja una vez que la nueva esté confirmada exitosamente en Calendar y DB.

### 2. Divergencia entre PostgreSQL y Google Calendar (SVR: MEDIA)
- **Ubicación**: `src/nodes.py:590-606`
- **Problema**: En `node_book_appointment`, si la creación del evento en Google Calendar tiene éxito pero la inserción en la base de datos de PostgreSQL falla, el sistema marca la cita como confirmada.
- **Comportamiento Actual**: El evento existe en Calendar pero no en DB.
- **Solución Sugerida**: Convertir la creación en DB en una operación bloqueante o implementar un mecanismo de compensación (rollback en Calendar si la DB falla).

---

## ⚠️ Riesgos Medios y Casos de Borde

### 3. Condición de Carrera en Disponibilidad (SVR: MEDIA)
- **Ubicación**: `src/nodes.py:340-410`
- **Problema**: No hay un mecanismo de "bloqueo temporal" (soft lock). Un slot puede ser mostrado como disponible, pero ser tomado por otro paciente antes de que el usuario actual complete la confirmación.
- **Solución Sugerida**: Re-verificar la disponibilidad del slot exactamente en el momento de la creación del evento (`node_book_appointment`).

### 4. Coincidencia de Slots Frágil (SVR: MEDIA)
- **Ubicación**: `src/edges.py:37-44`
- **Problema**: El "Auto-Booking" requiere una igualdad exacta de tiempo. Cualquier diferencia mínima de segundos o formato entre la preferencia del LLM y el slot de Calendar resultará en un fallo de coincidencia, forzando al usuario a elegir manualmente.
- **Solución Sugerida**: Implementar un margen de tolerancia (ej. $\pm 5$ minutos) o normalizar ambos valores a bloques de 15/30 min.

### 5. Hardcoding de Zona Horaria (SVR: MEDIA)
- **Ubicación**: `src/state.py:30`
- **Problema**: Se usa `America/Guayaquil` globalmente. Si el sistema se expande o atiende pacientes en otras zonas, habrá errores de horario.
- **Solución Sugerida**: Mover la zona horaria a la configuración del proyecto o detectar la zona del usuario.

---

## ℹ️ Mejoras de Experiencia (SVR: BAJA)

### 6. Ajuste Silencioso de Fin de Semana (SVR: BAJA)
- **Ubicación**: `src/nodes.py:364-367`
- **Problema**: Si el usuario pide sábado/domingo, el sistema mueve la cita al lunes sin avisar. El usuario podría recibir una confirmación para un día que no solicitó.
- **Solución Sugerida**: Marcar la fecha como `adjusted` en el estado y hacer que el LLM informe al usuario: *"Como no trabajamos fines de semana, he movido su cita al lunes..."*.

### 7. Extracción de Slots Simplista (SVR: BAJA)
- **Ubicación**: `src/state.py:379-393`
- **Problema**: La extracción de slots depende de regex simples y coincidencia de strings. No maneja bien lenguaje natural complejo (ej: "a media mañana").
- **Solución Sugerida**: Integrar una librería de parsing de fechas más robusta (como `dateparser`).

---

## Tabla de Prioridades

| Problema | Severidad | Tipo | Acción Recomendada |
| :--- | :---: | :---: | :--- |
| **Atomicidad Reagendar** | 🔴 Alta | Lógica | Invertir orden de operación |
| **Sincronización DB/Cal** | 🟡 Media | Consistencia | Hacer DB blocking o sync task |
| **Race Condition Slots** | 🟡 Media | Concurrencia | Re-verificación pre-booking |
| **Matching de Slots** | 🟡 Media | Lógica | Tolerancia de tiempo / Normalización |
| **Zona Horaria** | 🟡 Media | Arquitectura | Configuración dinámica por proyecto |
| **Aviso Fin de Semana** | 🟢 Baja | UX | Notificar ajuste de fecha |
| **Parsing de Slots** | 🟢 Baja | Robustez | Librería de parsing natural |
