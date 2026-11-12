#!/bin/bash
# JARVIS in the background: server + "Hey JARVIS" listener, no browser tab.
# Run from a normal Terminal (or at login). Ctrl-C stops both.
cd "$(dirname "$0")" || exit 1
export PATH="$HOME/.local/bin:$PATH"
unset CLAUDECODE CLAUDE_CODE_SESSION_ID CLAUDE_CODE_ENTRYPOINT ANTHROPIC_AUTH_TOKEN ANTHROPIC_API_KEY ANTHROPIC_BASE_URL
PORT="${JARVIS_PORT:-$(grep -s '^JARVIS_PORT=' .env | cut -d= -f2)}"; PORT="${PORT:-8730}"
up() { curl -s -m 2 -H "Host: localhost:$PORT" "http://127.0.0.1:$PORT/api/status" >/dev/null; }

if up; then
  echo "server already running on :$PORT"
else
  for _ in 1 2 3 4 5 6 7 8 9 10; do lsof -ti tcp:"$PORT" >/dev/null 2>&1 || break; sleep 0.5; done
  JARVIS_OPEN=0 .venv/bin/python -W ignore server.py &
  SERVER_PID=$!
  trap 'kill $SERVER_PID 2>/dev/null' EXIT
  for _ in $(seq 1 20); do up && break; sleep 0.5; done
  up || { echo "server failed to start" >&2; exit 1; }
fi
# the free on-device voice (Kokoro on the GPU), in the background
if [ -x .kokoro/bin/python ] && ! curl -s -m 1 http://127.0.0.1:8732/health >/dev/null; then
  .kokoro/bin/python -W ignore kokoro_service.py &
  KOKORO_PID=$!
fi
# the pop-up (menu bar ◉ + double-tap Control), in the background
pkill -f "popup.py" 2>/dev/null
.venv/bin/python -W ignore popup.py &
POPUP_PID=$!
trap 'kill $POPUP_PID $SERVER_PID $KOKORO_PID 2>/dev/null' EXIT
# give the voice a moment to load, so the listener's ready-made phrases use it
for _ in $(seq 1 30); do curl -s -m 1 http://127.0.0.1:8732/health >/dev/null && break; sleep 0.5; done
.venv/bin/python -W ignore listener.py
