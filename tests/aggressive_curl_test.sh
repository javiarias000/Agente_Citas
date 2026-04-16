#!/bin/bash

###############################################################################
#
# AGGRESSIVE CURL TEST SUITE — WhatsApp Webhook Stress Test
#
# Valida TODO el flujo enviando mensajes reales a +593984865981
# - Agendamiento completo
# - Reagendamiento
# - Cancelación
# - Concurrencia
# - Edge cases
#
###############################################################################

set -e

WEBHOOK_URL="${WEBHOOK_URL:-http://localhost:8000/webhook/whatsapp}"
PHONE="+593984865981"
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Contador de tests
TOTAL=0
PASSED=0
FAILED=0

# Log file
LOG_FILE="/tmp/curl_test_$(date +%s).log"

echo "═══════════════════════════════════════════════════════════════════════════"
echo "🧪 AGGRESSIVE CURL TEST SUITE"
echo "═══════════════════════════════════════════════════════════════════════════"
echo "Webhook: $WEBHOOK_URL"
echo "Phone: $PHONE"
echo "Log: $LOG_FILE"
echo ""

###############################################################################
# HELPERS
###############################################################################

log() {
    echo "[$(date '+%H:%M:%S')] $1" | tee -a "$LOG_FILE"
}

send_message() {
    local msg="$1"
    local test_name="$2"

    TOTAL=$((TOTAL + 1))

    log "📨 Test $TOTAL: $test_name"
    log "   Message: $msg"

    RESPONSE=$(curl -s -w "\n%{http_code}" -X POST "$WEBHOOK_URL" \
        -H "Content-Type: application/json" \
        -d "{
            \"sender\": \"$PHONE\",
            \"message\": \"$msg\",
            \"message_type\": \"text\"
        }" 2>&1)

    HTTP_CODE=$(echo "$RESPONSE" | tail -1)
    BODY=$(echo "$RESPONSE" | head -n -1)

    log "   Response: $HTTP_CODE"
    log "   Body: $BODY"

    # ✅ Validar respuesta
    if [[ "$HTTP_CODE" == "200" ]]; then
        log "   ✅ PASSED"
        PASSED=$((PASSED + 1))
        echo ""
        return 0
    else
        log "   ❌ FAILED (HTTP $HTTP_CODE)"
        FAILED=$((FAILED + 1))
        echo ""
        return 1
    fi
}

###############################################################################
# TEST SUITE
###############################################################################

echo -e "${BLUE}═ FLUJO 1: AGENDAMIENTO COMPLETO ═${NC}"
send_message "Hola quiero agendar" "Entry"
sleep 0.5
send_message "Limpeza dental" "Extract service"
sleep 0.5
send_message "Mañana a las 10" "Extract date/time"
sleep 0.5
send_message "Sí, confirmar" "Confirm booking"
sleep 1

echo -e "${BLUE}═ FLUJO 2: REAGENDAMIENTO ═${NC}"
send_message "Quiero cambiar mi cita" "Request reschedule"
sleep 0.5
send_message "Para el viernes a las 14:00" "New datetime"
sleep 0.5
send_message "Sí, cambiar" "Confirm reschedule"
sleep 1

echo -e "${BLUE}═ FLUJO 3: CANCELACIÓN ═${NC}"
send_message "Cancelar cita" "Request cancel"
sleep 0.5
send_message "Sí, cancelar" "Confirm cancel"
sleep 1

echo -e "${BLUE}═ FLUJO 4: EDGE CASES ═${NC}"
send_message "Agendar para el sábado" "Weekend handling"
sleep 0.5
send_message "Confirmar" "Weekend confirm"
sleep 0.5
send_message "Mostrar horarios" "Availability check"
sleep 0.5
send_message "A las 13:00" "Select specific time"
sleep 0.5
send_message "OK" "Confirm time"
sleep 1

echo -e "${BLUE}═ FLUJO 5: RAPID-FIRE (5 mensajes seguidos) ═${NC}"
for i in {1..5}; do
    send_message "Mensaje rápido $i" "Rapid-fire message $i"
    sleep 0.1
done
sleep 1

echo -e "${BLUE}═ FLUJO 6: INVALID INPUTS ═${NC}"
send_message "" "Empty message"
sleep 0.5
send_message "123456789" "Numbers only"
sleep 0.5
send_message "Agendar para el 31 de febrero" "Invalid date"
sleep 0.5
send_message "Especialidad que no existe" "Invalid service"
sleep 1

echo -e "${BLUE}═ FLUJO 7: CONCURRENT (Background processes) ═${NC}"
send_message "Request 1" "Concurrent message 1" &
PID1=$!
send_message "Request 2" "Concurrent message 2" &
PID2=$!
send_message "Request 3" "Concurrent message 3" &
PID3=$!
wait $PID1 $PID2 $PID3
sleep 1

echo -e "${BLUE}═ FLUJO 8: MIXED SCENARIOS ═${NC}"
send_message "Hola" "Greeting"
sleep 0.3
send_message "Agendar ortodoncia" "Service: ortodoncia"
sleep 0.3
send_message "Pasado mañana" "Date: pasado mañana"
sleep 0.3
send_message "15:00" "Time: 15:00"
sleep 0.3
send_message "Confirmar" "Confirm"
sleep 0.3
send_message "Cambiar" "Request reschedule"
sleep 0.3
send_message "Próxima semana" "New date"
sleep 0.3
send_message "OK" "Confirm reschedule"
sleep 1

###############################################################################
# RESULTS
###############################################################################

echo ""
echo "═══════════════════════════════════════════════════════════════════════════"
echo "📊 RESULTS"
echo "═══════════════════════════════════════════════════════════════════════════"
echo -e "Total tests: $TOTAL"
echo -e "${GREEN}Passed: $PASSED${NC}"
echo -e "${RED}Failed: $FAILED${NC}"

if [ $FAILED -eq 0 ]; then
    echo -e "${GREEN}✅ ALL TESTS PASSED${NC}"
    exit 0
else
    echo -e "${RED}❌ SOME TESTS FAILED${NC}"
    echo "Log saved: $LOG_FILE"
    exit 1
fi
