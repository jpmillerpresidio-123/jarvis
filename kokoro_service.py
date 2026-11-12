"""
JARVIS's free, on-device voice: Kokoro-82M on the Mac's GPU (via MLX).

Runs in its own Python 3.12 environment (.kokoro) because Kokoro needs a newer
Python than the rest of JARVIS. Listens on 127.0.0.1 only; server.py's
voice.speak() calls it.

    POST /speak  {"text": "...", "voice": "bm_george"}  ->  audio/wav
    GET  /health                                         ->  {"ok": true}

No account, no credits, nothing leaves the Mac. About half a second of work
per sentence on an M-series Mac.
"""
import io
import json
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import numpy as np
import soundfile as sf
from mlx_audio.tts.utils import load_model

_env = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
for _line in open(_env, encoding="utf-8") if os.path.exists(_env) else []:
    _line = _line.strip()
    if _line and not _line.startswith("#") and "=" in _line:
        _k, _, _v = _line.partition("=")
        os.environ.setdefault(_k.strip(), _v.strip().strip('"').strip("'"))

PORT = int(os.environ.get("JARVIS_KOKORO_PORT", "8732"))
VOICE = os.environ.get("JARVIS_KOKORO_VOICE", "bm_george")
SPEED = float(os.environ.get("JARVIS_KOKORO_SPEED", "1.05"))
MODEL = load_model("mlx-community/Kokoro-82M-bf16")
LOCK = threading.Lock()              # one sentence at a time on the GPU


def synth(text, voice):
    lang = "b" if voice.startswith("b") else "a"     # b = British English, a = American
    with LOCK:
        chunks = [np.array(r.audio).reshape(-1)
                  for r in MODEL.generate(text=text, voice=voice, speed=SPEED, lang_code=lang)]
    buf = io.BytesIO()
    sf.write(buf, np.concatenate(chunks) if chunks else np.zeros(2400), 24000, format="WAV")
    return buf.getvalue()


synth("Ready.", VOICE)               # warm up, so the first real sentence is fast


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, code, body, ctype):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == "/health":
            return self._send(200, b'{"ok": true}', "application/json")
        self._send(404, b"{}", "application/json")

    def do_POST(self):
        if self.path != "/speak":
            return self._send(404, b"{}", "application/json")
        try:
            d = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
            text = (d.get("text") or "").strip()[:2500]
            if not text:
                return self._send(400, b'{"error": "empty text"}', "application/json")
            self._send(200, synth(text, d.get("voice") or VOICE), "audio/wav")
        except Exception as e:                       # noqa: BLE001
            self._send(500, json.dumps({"error": str(e)[:200]}).encode(), "application/json")


if __name__ == "__main__":
    print(f"kokoro voice ready on 127.0.0.1:{PORT} ({VOICE})", flush=True)
    ThreadingHTTPServer(("127.0.0.1", PORT), H).serve_forever()
