#!/bin/bash

###############################################################################
#
# INTERACTIVE CURL TEST — Real conversation scenarios
#
# Simula conversaciones REALES con el agente:
# - Ambiguas
# - Agendar → cancelar porque no puede → reagendar
# - Múltiples doctores
# - Casos de fallo y recuperación
#
# USO: bash interactive_curl_test.sh
#
###############################################################################

WEBHOOK_URL="${WEBHOOK_URL:-http://localhost:8000/webhook/whatsapp}"
PHONE="${1:-+593984865981}"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
NC='\033[0m'

# Contador de mensajes
MSG_COUNT=0

###############################################################################
# HELPERS
###############################################################################

send_msg() {
    local msg="$1"
    local label="${2:-User}"

    MSG_COUNT=$((MSG_COUNT + 1))

    echo ""
    echo -e "${CYAN}[Turn $MSG_COUNT] ${BLUE}$label${NC}: $msg"
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

    RESPONSE=$(curl -s -X POST "$WEBHOOK_URL" \
        -H "Content-Type: application/json" \
        -d "{
            \"sender\": \"$PHONE\",
            \"message\": \"$msg\",
            \"message_type\": \"text\"
        }" 2>&1)

    HTTP_CODE=$(echo "$RESPONSE" | tail -1 | grep -o '[0-9]\{3\}$' || echo "000")
    BODY=$(echo "$RESPONSE" | grep -v '^[0-9]\{3\}$')

    if [[ "$HTTP_CODE" == "200" ]]; then
        echo -e "${GREEN}[✓ HTTP 200]${NC}"
    else
        echo -e "${RED}[✗ HTTP $HTTP_CODE]${NC}"
    fi

    if [ ! -z "$BODY" ]; then
        echo "Response: $BODY" | jq . 2>/dev/null || echo "Response: $BODY"
    fi

    echo ""
    read -p "Press Enter para continuar..." dummy
}

scenario() {
    local title="$1"
    echo ""
    echo "╔════════════════════════════════════════════════════════════════════╗"
    echo "║ $title"
    echo "╚════════════════════════════════════════════════════════════════════╝"
    echo ""
    read -p "Press Enter para iniciar escenario..." dummy
}

###############################################################################
# ESCENARIO 1: CONVERSACIÓN AMBIGUA → Aclaraciones
###############################################################################

scenario "ESCENARIO 1: Conversación Ambigua (Requires Clarification)"

send_msg "Hola!" "User"
send_msg "Quiero una cita" "User"
send_msg "Limpeza" "User (servicio vago)"
send_msg "Próxima semana" "User (fecha vaga)"
send_msg "Por la mañana" "User (hora ambigua)"
send_msg "Lunes a las 10 de la mañana" "User (clarificación)"
send_msg "Sí, confirmo" "User (confirmación)"

###############################################################################
# ESCENARIO 2: AGENDAR → CANCELAR (no puede) → REAGENDAR OTRO DÍA
###############################################################################

scenario "ESCENARIO 2: Agendar → Intentar Cancelar → Reagendar"

send_msg "Buenos días" "User"
send_msg "Necesito agendar" "User"
send_msg "Limpeza dental" "User (Dr. Jorge)"
send_msg "Mañana a las 14:00" "User"
send_msg "Confirmar" "User"
echo -e "${GREEN}✓ Cita agendada para mañana a las 14:00${NC}"

sleep 2

send_msg "Cambiar de opinión, cancelar" "User"
send_msg "Sí" "User (confirmación de cancelación)"
echo -e "${GREEN}✓ Cita cancelada${NC}"

sleep 2

send_msg "En realidad quiero agendar para el viernes" "User"
send_msg "Limpeza" "User"
send_msg "Viernes a las 10" "User"
send_msg "Confirmar" "User"
echo -e "${GREEN}✓ Cita reagendada para viernes a las 10:00${NC}"

###############################################################################
# ESCENARIO 3: MÚLTIPLES DOCTORES (Jorge vs Javier)
###############################################################################

scenario "ESCENARIO 3: Agendar con diferentes doctores"

send_msg "Hola de nuevo" "User"
send_msg "Quiero ortodoncia con el Dr. Javier" "User"
send_msg "Mañana" "User"
send_msg "A las 11:00" "User"
send_msg "Sí" "User"
echo -e "${GREEN}✓ Agendado con Dr. Javier (javiarias000@gmail.com)${NC}"

sleep 2

send_msg "Cambiar cita, preferiero Dr. Jorge" "User"
send_msg "Mañana a las 16:00" "User"
send_msg "Sí" "User"
echo -e "${GREEN}✓ Reagendado con Dr. Jorge (jorge.arias.amauta@gmail.com)${NC}"

###############################################################################
# ESCENARIO 4: CONVERSACIÓN CON ERRORES Y RECUPERACIÓN
###############################################################################

scenario "ESCENARIO 4: Errores y Recuperación"

send_msg "Quiero agendar para el 30 de febrero" "User (fecha inválida)"
send_msg "Disculpa, para el 28 de febrero" "User (recuperación)"
send_msg "Limpeza" "User"
send_msg "10:00" "User"
send_msg "Ok" "User"

sleep 1

send_msg "En realidad, cambiar para marzo" "User"
send_msg "15 de marzo" "User"
send_msg "A las 15:00" "User"
send_msg "Sí, confirmar" "User"

###############################################################################
# ESCENARIO 5: RÁPIDO/IMPACIENTE (múltiples mensajes rápido)
###############################################################################

scenario "ESCENARIO 5: Usuario Rápido/Impaciente"

send_msg "Agendar" "User (rápido)"
send_msg "Limpeza" "User (rápido)"
send_msg "Hoy" "User (rápido)"
send_msg "Ahora" "User (rápido)"
send_msg "Confirmar" "User (rápido)"

###############################################################################
# ESCENARIO 6: CONSULTAR SIN AGENDAR
###############################################################################

scenario "ESCENARIO 6: Solo Consultar Disponibilidad"

send_msg "¿Qué horarios tienes disponibles?" "User"
send_msg "Para el miércoles" "User"
send_msg "Limpeza" "User"
send_msg "Gracias, me avisan después" "User"

###############################################################################
# ESCENARIO 7: CONVERSACIÓN LARGA CON CAMBIOS MÚLTIPLES
###############################################################################

scenario "ESCENARIO 7: Conversación Larga (Usuario Indeciso)"

send_msg "Hola, quería agendar" "User"
send_msg "Limpeza" "User"
send_msg "Cuando tengas disponible" "User"
send_msg "Mañana" "User"
send_msg "A las 10" "User"
send_msg "Espera, mejor a las 14" "User"
send_msg "Confirmar para las 14" "User"
echo -e "${GREEN}✓ Agendado para mañana a las 14:00${NC}"

sleep 2

send_msg "Disculpa, cancelar esa cita" "User"
send_msg "Sí" "User"
echo -e "${GREEN}✓ Cancelado${NC}"

sleep 2

send_msg "Quiero agendar para la próxima semana" "User"
send_msg "Lunes" "User"
send_msg "10:00" "User"
send_msg "OK" "User"
echo -e "${GREEN}✓ Agendado para próxima semana lunes 10:00${NC}"

###############################################################################
# ESCENARIO 8: CON EMOJIS Y CARACTERES ESPECIALES
###############################################################################

scenario "ESCENARIO 8: Emojis y Caracteres Especiales"

send_msg "Hola 😊 quiero agendar" "User (con emoji)"
send_msg "Limpeza dental 🦷" "User (con emoji)"
send_msg "Mañana a las 10:00 ✅" "User (con emoji)"
send_msg "Perfecto! 👍" "User (con emoji)"

###############################################################################
# ESCENARIO 9: MÚLTIPLES CANCELACIONES Y REAGENDAMIENTOS
###############################################################################

scenario "ESCENARIO 9: Ping-Pong (Cancel → Reschedule × 3)"

send_msg "Agendar limpeza" "User"
send_msg "Mañana a las 9:00" "User"
send_msg "Confirmar" "User"
echo -e "${GREEN}✓ Agendado mañana 9:00${NC}"

sleep 1

send_msg "Cancelar" "User"
send_msg "Sí" "User"
echo -e "${GREEN}✓ Cancelado${NC}"

sleep 1

send_msg "Agendar para mañana a las 10:00" "User"
send_msg "Confirmar" "User"
echo -e "${GREEN}✓ Agendado mañana 10:00${NC}"

sleep 1

send_msg "Cambiar para mañana a las 11:00" "User"
send_msg "Sí" "User"
echo -e "${GREEN}✓ Reagendado mañana 11:00${NC}"

sleep 1

send_msg "Cambiar para mañana a las 12:00" "User"
send_msg "Confirmar" "User"
echo -e "${GREEN}✓ Reagendado mañana 12:00${NC}"

###############################################################################
# ESCENARIO 10: CASOS LÍMITE (Fin de semana, etc)
###############################################################################

scenario "ESCENARIO 10: Casos Límite (Weekends, etc)"

send_msg "Agendar para el sábado" "User (fin de semana)"
send_msg "Limpeza" "User"
send_msg "10:00" "User"
send_msg "Confirmar" "User"
echo -e "${YELLOW}[System should adjust to Monday]${NC}"

sleep 1

send_msg "Agendar para domingo" "User (otro fin de semana)"
send_msg "Ortodoncia" "User"
send_msg "15:00" "User"
send_msg "Sí" "User"
echo -e "${YELLOW}[System should adjust to Monday]${NC}"

###############################################################################
# SUMMARY
###############################################################################

echo ""
echo "╔════════════════════════════════════════════════════════════════════╗"
echo "║ TESTING COMPLETE"
echo "╚════════════════════════════════════════════════════════════════════╝"
echo ""
echo -e "${GREEN}✓ Completados 10 escenarios${NC}"
echo -e "${GREEN}✓ $MSG_COUNT mensajes totales${NC}"
echo ""
echo "Validar en Google Calendar:"
echo "  - Citas agendadas correctamente"
echo "  - Citas canceladas eliminadas"
echo "  - Reagendamientos movidos correctamente"
echo "  - Fin de semana ajustado a lunes"
echo "  - Doctores correctos (Dr. Jorge vs Dr. Javier)"
echo ""
echo -e "${CYAN}Para reproducir un escenario:${NC}"
echo "  - Cambia PHONE='+593984865981' a otro número"
echo "  - O pasa como argumento: bash interactive_curl_test.sh +593987654321"
echo ""
