#!/bin/bash
# One-time setup for JARVIS on an Apple Silicon Mac. Safe to re-run.
#   ./setup.sh
set -e
cd "$(dirname "$0")"
say_step() { printf "\n\033[1;36m▸ %s\033[0m\n" "$1"; }

[ "$(uname -s)" = Darwin ] && [ "$(uname -m)" = arm64 ] || { echo "JARVIS needs an Apple Silicon Mac (M1 or newer)."; exit 1; }
xcode-select -p >/dev/null 2>&1 || { echo "Install Apple's command line tools first: xcode-select --install  (then re-run ./setup.sh)"; exit 1; }
command -v claude >/dev/null 2>&1 || [ -x "$HOME/.local/bin/claude" ] || {
  echo "Install Claude Code first: https://claude.com/claude-code  — then run 'claude' once and sign in."; exit 1; }

say_step "Python packages for JARVIS (.venv)"
[ -x .venv/bin/python ] || /usr/bin/python3 -m venv .venv
.venv/bin/pip install -q --upgrade pip
.venv/bin/pip install -q -r requirements.txt

say_step "Wake word model (\"Hey JARVIS\")"
.venv/bin/python -W ignore -c "import openwakeword.utils as u; u.download_models(['hey_jarvis'])"

say_step "Free on-device voice: Kokoro (.kokoro, Python 3.12)"
[ -x .kokoro/bin/python ] || .venv/bin/uv venv -q --python 3.12 .kokoro
VIRTUAL_ENV=.kokoro .venv/bin/uv pip install -q -r requirements-voice.txt
# espeak-ng can't read its data from long folder paths, so keep a copy at a short one
DATA=$(.kokoro/bin/python -c "import espeakng_loader, os; print(os.path.join(os.path.dirname(espeakng_loader.__file__), 'espeak-ng-data'))")
mkdir -p "$HOME/.jarvis"; rm -rf "$HOME/.jarvis/espeak-ng-data"; cp -R "$DATA" "$HOME/.jarvis/espeak-ng-data"
SITE=$(.kokoro/bin/python -c "import site; print(site.getsitepackages()[0])")
cat > "$SITE/sitecustomize.py" <<'PY'
import os as _os
_short = _os.path.expanduser("~/.jarvis/espeak-ng-data")
if _os.path.isdir(_short):
    try:
        import espeakng_loader as _el
        _el.get_data_path = lambda: _short
    except Exception:
        pass
PY
echo "  downloading the voice model (one time, ~330 MB)…"
.kokoro/bin/python -W ignore -c "from mlx_audio.tts.utils import load_model; m=load_model('mlx-community/Kokoro-82M-bf16'); [r for r in m.generate(text='Ready.', voice='bm_george', lang_code='b')]" >/dev/null 2>&1 \
  && echo "  voice OK" || echo "  (voice test failed — JARVIS will fall back to the Mac's built-in voice)"

say_step "Settings"
[ -f .env ] || { cp .env.example .env; echo "  created .env — open it and put your first name in JARVIS_USER_NAME"; }

say_step "The JARVIS app"
[ "${JARVIS_NO_APP:-0}" = 1 ] || ./make_app.sh

cat <<'MSG'

Done. Next:
  1. Open .env and set JARVIS_USER_NAME (and teams, if you like).
  2. Open JARVIS from ~/Applications (or Spotlight).
  3. Allow the microphone when macOS asks.
  4. Say "Hey JARVIS".
The first question also downloads the hearing model (~480 MB), so give it a minute.
MSG
