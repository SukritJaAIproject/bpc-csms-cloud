#!/bin/sh
# Container start: optional demo simulators, then the CSMS on $PORT.
#   $PORT  web console, BPC EV app (/app/), pay page (/pay/...) and OCPP 1.6-J: ws(s)://<host>/ocpp/<charge point id>
#   The built-in simulators (CSMS_SIMULATORS="ID1 ID2 ..."; empty = none) connect the same way: ws://127.0.0.1:$PORT/ocpp
cd /app || exit 1
PORT="${PORT:-10000}"
# Render provides the public address of the service; QR stickers, the app poster and payment return URLs use it
if [ -z "$CSMS_PUBLIC_URL" ] && [ -n "$RENDER_EXTERNAL_URL" ]; then
    CSMS_PUBLIC_URL="$RENDER_EXTERNAL_URL"
    export CSMS_PUBLIC_URL
fi
mkdir -p logs
i=0
for id in ${CSMS_SIMULATORS:-}; do
    i=$((i + 1))
    python mock_cp.py --url "ws://127.0.0.1:${PORT}/ocpp" --id "$id" --panel-host 127.0.0.1 --panel-port $((8100 + i)) \
        --speed "${CSMS_SIM_SPEED:-20}" --no-browser --log "logs/sim_${id}.log" >/dev/null 2>&1 &
done
if [ "$i" -gt 0 ]; then
    echo "started $i simulated charger(s): ${CSMS_SIMULATORS}"
fi
# CSMS_OCPP_PORT=0 (default here): no separate OCPP port - OCPP only on the web port, the one public port on Render
exec python csms_web_ui_flexxfast1.py --csms-child 1.6 --port "${CSMS_OCPP_PORT:-0}" --web-port "$PORT"
