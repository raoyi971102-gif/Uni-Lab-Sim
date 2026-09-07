#!/bin/bash
cd "$(dirname "$0")"
PY="${PY:-python3}"
OPCUA_URL="${1:-opc.tcp://127.0.0.1:4855/xuse_sim/}"
"$PY" "./xuse_handshake_agent.py" --url "$OPCUA_URL" --config "./config/xuse_handshake.yaml"
