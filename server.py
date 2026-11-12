"""
JARVIS Live — a HUD that drives Claude Code (Path A).

    python3 server.py

No API key needed for the brain: it runs your own `claude` CLI, so it uses your
Claude Code subscription and every MCP connector / skill you've configured. The
ElevenLabs key (in .env) is only for voice, and only ever lives server-side.
"""
import json
import mimetypes
import os
import pathlib
import sys
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

ROOT = pathlib.Path(__file__).resolve().parent
UI = ROOT / "ui"

# load .env before importing anything that reads os.environ
_env = ROOT / ".env"
if _env.exists():
    for line in _env.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, _, v = line.partition("=")
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))

import commands         # noqa: E402
import remote_auth      # noqa: E402
import brief            # noqa: E402
import runtime          # noqa: E402
import voice            # noqa: E402

PORT = int(os.environ.get("JARVIS_PORT", "8730"))
# The tunnel's public hostname (e.g. my-jarvis.ngrok-free.app). Web requests for
# any other hostname are refused, so the tunnel is the only way in from outside.
PUBLIC_HOST = os.environ.get("JARVIS_PUBLIC_HOST", "").strip().lower()
MAX_BODY = 15 * 1024 * 1024
FOCUS_FLAG = "/tmp/jarvis-page-focus"      # "1" while the JARVIS page is in front
REMOTE_NOTE = """## Remote session

This turn comes from the owner's phone or another computer over the web, not the Mac.
- You can read mail, calendar, Drive and assignments, search the web, write Gmail
  DRAFTS and create Google Docs. You cannot run commands on the Mac, send or
  delete email, change calendar events, or touch Shopify; if asked, say it has to
  be done from the Mac.
- Don't "open" anything on the Mac. Give the link instead ("Here's your document:
  <link>")."""

# The Jarvis persona, appended to every run. Without it, the model answers as a
# coding agent and narrates its own tooling — which is not what you want spoken
# out loud. Edit persona.md to change how it talks.
_PERSONA_FILE = ROOT / "persona.md"


def persona():
    try:
        base = _PERSONA_FILE.read_text(encoding="utf-8").strip()
        base = base.replace("{TOOLS}", str(ROOT / "tools"))
        base = base.replace("{NAME}", os.environ.get("JARVIS_USER_NAME", "").strip() or "the user")
    except OSError:
        base = ""
    extra = commands.context_block()      # profile / goal / personality / queue
    return "\n\n".join(x for x in (base, extra) if x)
# one continuing Claude Code conversation until the user hits /new
SESSION = {"id": None}

# ── live feed: runs that come from the voice listener or the pop-up (not this
# page) are mirrored here, so an open dashboard shows "Hey JARVIS" turns too.
import collections   # noqa: E402
import threading     # noqa: E402
FEED = collections.deque(maxlen=400)
FEED_CV = threading.Condition()
FEED_SEQ = [0]


def feed_push(ev):
    with FEED_CV:
        FEED_SEQ[0] += 1
        FEED.append((FEED_SEQ[0], ev))
        FEED_CV.notify_all()


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        if os.environ.get("JARVIS_VERBOSE"):
            sys.stderr.write("  " + (fmt % args) + "\n")

    # ── helpers ──────────────────────────────────────────────
    def _remote(self):
        """True for anything that came through the tunnel (or isn't plain localhost)."""
        if self.headers.get("X-Forwarded-For") or any(k.lower().startswith("ngrok") for k in self.headers.keys()):
            return True
        return self.headers.get("Host") not in {f"localhost:{PORT}", f"127.0.0.1:{PORT}"}

    def _remote_ok(self, need_session=True):
        """Remote requests: right hostname, same-origin, and (usually) signed in."""
        host = (self.headers.get("Host") or "").lower()
        if not PUBLIC_HOST or host != PUBLIC_HOST:
            return False
        origin = self.headers.get("Origin")
        if origin and urlparse(origin).netloc.lower() != PUBLIC_HOST:
            return False
        return (not need_session) or remote_auth.valid(self.headers.get("Cookie"))

    def _client_ip(self):
        return (self.headers.get("X-Forwarded-For") or self.client_address[0]).split(",")[0].strip()

    def _allowed(self):
        return self._remote_ok() if self._remote() else self._local()

    def _local(self):
        """True when the request comes from this HUD's own page.

        Without this, any website open in the browser could POST to
        localhost and run the assistant — with permission prompts off.
        """
        ok = {f"localhost:{PORT}", f"127.0.0.1:{PORT}"}
        origin = self.headers.get("Origin")
        if self.headers.get("Host") not in ok:
            return False
        return origin is None or urlparse(origin).netloc in ok

    def end_headers(self):
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("X-Robots-Tag", "noindex, nofollow")
        super().end_headers()

    def _json(self, body, code=200, cookie=None):
        data = json.dumps(body).encode()
        self.send_response(code)
        if cookie:
            self.send_header("Set-Cookie", cookie)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def _bytes(self, data, ctype, code=200):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def _read(self):
        n = int(self.headers.get("Content-Length", 0) or 0)
        if n > MAX_BODY:
            raise ValueError("request too large")
        return self.rfile.read(n) if n else b""

    # ── GET ──────────────────────────────────────────────────
    def do_GET(self):
        p = urlparse(self.path).path
        if self._remote() and not self._remote_ok():
            if not self._remote_ok(need_session=False):
                return self._json({"error": "forbidden"}, 403)
            if p in ("/login", "/login.html"):
                return self._bytes((UI / "login.html").read_bytes(), "text/html; charset=utf-8")
            if p == "/robots.txt":
                return self._bytes(b"User-agent: *\nDisallow: /\n", "text/plain")
            if p.startswith("/api/"):
                return self._json({"error": "sign in first"}, 401)
            self.send_response(302)
            self.send_header("Location", "/login")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return None
        if not self._allowed():
            return self._json({"error": "forbidden"}, 403)
        if p == "/api/voices":
            return self._json({"voices": voice.voices()})
        if p == "/api/status":
            return self._json(dict(
                runtime=runtime.runtime_kind(),
                permission=runtime.PERMISSION,
                workdir=runtime.WORKDIR,
                model=runtime.MODEL or "(claude default)",
                stt="on-device" if voice.STT != "elevenlabs" or not voice.eleven_ok() else "elevenlabs",
                tts=voice.health()["voice"],
                voice_health=voice.health(),
                session=SESSION["id"]))
        if p == "/api/feed":
            # long-poll: wait up to 20s for events newer than ?since=N
            from urllib.parse import parse_qs
            try:
                since = int(parse_qs(urlparse(self.path).query).get("since", ["-1"])[0])
            except ValueError:
                since = -1
            with FEED_CV:
                if since >= 0:
                    FEED_CV.wait_for(lambda: FEED_SEQ[0] > since, timeout=20)
                events = [dict(ev, seq=s) for s, ev in FEED if since >= 0 and s > since]
                seq = FEED_SEQ[0]
            try:
                return self._json({"seq": seq, "events": events})
            except (BrokenPipeError, ConnectionResetError):
                return None                    # the page/pop-up closed mid-poll; harmless
        if p == "/api/brief":
            return self._json(brief.everything())
        if p == "/api/jobs":
            # finished background missions, reported once each
            return self._json(dict(done=commands.take_finished(),
                                   running=[j for j in commands.jobs_snapshot()
                                            if j["status"] == "running"]))

        rel = "index.html" if p == "/" else p.lstrip("/")
        f = (UI / rel).resolve()
        if not str(f).startswith(str(UI.resolve())) or not f.is_file():
            return self._bytes(b"not found", "text/plain", 404)
        ctype = mimetypes.guess_type(f.name)[0] or "application/octet-stream"
        return self._bytes(f.read_bytes(), ctype)

    # ── POST ─────────────────────────────────────────────────
    def do_POST(self):
        p = urlparse(self.path).path
        try:
            raw = self._read()
        except ValueError:
            return self._json({"error": "too large"}, 413)
        if self._remote() and p == "/api/login":
            if not self._remote_ok(need_session=False):
                return self._json({"error": "forbidden"}, 403)
            try:
                pw = json.loads(raw or b"{}").get("password", "")
            except ValueError:
                pw = ""
            token, err = remote_auth.login(str(pw)[:200], self._client_ip())
            if not token:
                return self._json({"ok": False, "error": err}, 401)
            return self._json({"ok": True}, cookie=remote_auth.cookie_header(token))
        if not self._allowed():
            return self._json({"error": "forbidden"}, 403)
        if p == "/api/logout":
            remote_auth.logout(self.headers.get("Cookie"))
            return self._json({"ok": True}, cookie="jarvis_session=; Path=/; Max-Age=0; HttpOnly; Secure; SameSite=Strict")

        if p == "/api/speak":
            try:
                text = (json.loads(raw or b"{}").get("text") or "").strip()
                audio, mime = voice.speak_any(text)
                return self._bytes(audio, mime)
            except Exception as e:                        # noqa: BLE001
                return self._json({"error": str(e)[:200]}, 503)

        if p == "/api/listen":
            try:
                mime = self.headers.get("Content-Type", "audio/webm")
                if os.environ.get("JARVIS_DEBUG") == "1":
                    # keep the last clip so "nothing transcribed" can be checked by ear
                    with open("/tmp/jarvis-last-audio", "wb") as f:
                        f.write(raw)
                return self._json({"text": voice.transcribe(raw, mime)})
            except Exception as e:                        # noqa: BLE001
                return self._json({"error": str(e)[:300], "text": ""}, 503)

        if p == "/api/open-blackbaud":
            # TODAY panel arrow → Blackbaud Assignment Center in Chrome (this Mac only)
            if self._remote():
                return self._json({"ok": False}, 403)
            import subprocess
            if not brief.BLACKBAUD_URL:
                return self._json({"ok": False, "error": "set JARVIS_BLACKBAUD_URL in .env"}, 400)
            r = subprocess.run(["open", "-a", "Google Chrome", brief.BLACKBAUD_URL])
            if r.returncode != 0:
                subprocess.run(["open", brief.BLACKBAUD_URL])       # no Chrome: default browser
            return self._json({"ok": True})

        if p == "/api/standby":
            if self._remote():
                return self._json({"ok": False}, 403)
            try:
                frm = (json.loads(raw or b"{}") or {}).get("from")
            except Exception:                             # noqa: BLE001
                frm = None
            if frm == "button":
                # the dashboard button: make the listener stop talking/listening now,
                # and say "On standby." in the backup voice
                import subprocess
                subprocess.run(["pkill", "-USR2", "-f", "--", "ignore listener.py"])
                def _say():
                    try:
                        import tempfile
                        audio, mime = voice.speak_any("On standby.")
                        with tempfile.NamedTemporaryFile(suffix=".wav" if "wav" in mime else ".mp3",
                                                         delete=False) as f:
                            f.write(audio)
                        subprocess.run(["afplay", f.name]); os.unlink(f.name)
                    except Exception:                     # noqa: BLE001  (ElevenLabs down: macOS voice)
                        subprocess.run(["say", "-v", os.environ.get("JARVIS_FALLBACK_VOICE", "Daniel"), "On standby."])
                import threading as _t
                _t.Thread(target=_say, daemon=True).start()
            feed_push(dict(t="standby", src=frm or "voice"))
            return self._json({"ok": True})

        if p == "/api/wake":
            # dashboard VOICE button → start the background listener (this Mac only)
            if self._remote():
                return self._json({"ok": False, "error": "voice wake is Mac-only"}, 403)
            import subprocess
            ok = subprocess.run(["pkill", "-USR1", "-f", "ignore listener.py"]).returncode == 0
            return self._json({"ok": ok})

        if p == "/api/focus":
            # the JARVIS page is (or isn't) in front: the pop-up stays hidden and
            # voice goes hands-free while it is. A flag file, so listener.py and
            # popup.py can read it without asking the server.
            if self._remote():
                return self._json({"ok": True})
            try:
                on = bool(json.loads(raw or b"{}").get("on"))
            except ValueError:
                on = False
            with open(FOCUS_FLAG, "w") as f:
                f.write("1" if on else "0")
            return self._json({"ok": True})

        if p == "/api/new":
            SESSION["id"] = None
            if self._remote():
                runtime.CORE_REMOTE.reset()
            else:
                runtime.CORE.reset(persona())
            return self._json({"ok": True})

        if p == "/api/run":
            return self._stream_run(raw)

        return self._json({"error": "no such endpoint"}, 404)

    # ── the run: NDJSON stream of events ─────────────────────
    def _stream_run(self, raw):
        try:
            payload = json.loads(raw or b"{}")
        except json.JSONDecodeError:
            return self._json({"error": "bad json"}, 400)
        message = (payload.get("message") or "").strip()
        extra = "" if self._remote() else (payload.get("system") or "").strip()
        system = "\n\n".join(x for x in (persona(), extra) if x) or None
        fresh = bool(payload.get("fresh"))
        if not message:
            return self._json({"error": "empty message"}, 400)
        if fresh:
            SESSION["id"] = None
            runtime.CORE.reset()

        self.send_response(200)
        self.send_header("Content-Type", "application/x-ndjson")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "close")
        self.end_headers()

        src = (self.headers.get("X-Jarvis-Source") or "browser").strip().lower()[:20]
        mirror = src != "browser"
        if mirror:
            shown = message.split("] ", 1)[1] if message.startswith("[spoken") else message
            feed_push(dict(t="mirror_start", src=src, message=shown[:500]))

        def emit(ev):
            if mirror:
                feed_push(dict(ev, src=src))
            self.wfile.write((json.dumps(ev) + "\n").encode())
            self.wfile.flush()

        # ── the command matrix: /new /profile /goal /personality /kanban /background
        remote = self._remote()
        if remote:
            system = "\n\n".join(x for x in (persona(), REMOTE_NOTE) if x)
        cmd = commands.handle(
            message,
            runner=None if remote else (lambda m: runtime.run(m, None, persona())))
        if cmd:
            if cmd.get("note"):
                emit(dict(t="note", message=cmd["note"]))
            if cmd.get("fresh"):
                SESSION["id"] = None
                runtime.CORE.reset()
            if cmd.get("message") is None:
                # answered locally — no model call needed
                emit(dict(t="delta", text=cmd.get("reply", "Done.")))
                emit(dict(t="complete", ms=0))
                return
            message = cmd["message"]
            system = "\n\n".join(x for x in (persona(), REMOTE_NOTE if remote else extra) if x) or None

        try:
            # warm core: one long-lived claude process, so a turn skips the ~5s cold
            # start. JARVIS_WARM=0 falls back to one process per question.
            if remote:
                turns = runtime.CORE_REMOTE.ask(message, system)      # read + draft only
            elif os.environ.get("JARVIS_WARM", "1") != "0":
                turns = runtime.CORE.ask(message, system)
            else:
                turns = runtime.run(message, SESSION["id"], system)
            for ev in turns:
                sid = ev.get("session_id")
                # Only remember a session that actually COMPLETED. Storing it from
                # `status` (which fires at init) means one broken run poisons every
                # run after it with --resume <half-born session>.
                if ev.get("t") == "complete" and runtime.valid_session(sid):
                    SESSION["id"] = sid
                if ev.get("t") == "error":
                    SESSION["id"] = None       # drop a bad/stale session so the next run is fresh
                emit(ev)
        except (BrokenPipeError, ConnectionResetError):
            pass                                          # client navigated away
        except Exception as e:                            # noqa: BLE001
            try:
                emit(dict(t="error", message=str(e)[:300]))
            except OSError:
                pass


def main():
    kind = runtime.runtime_kind()
    brain = ("Claude Code (your subscription — connectors & skills live)"
             if kind == "claude" else "OFFLINE — claude CLI not found; runs will report an error")
    vo = (f"ElevenLabs · {voice.voice_id()[:8]}…" if voice.available()
          else "none (add ELEVENLABS_API_KEY for voice)")
    perm = runtime.PERMISSION
    perm_note = ("  can run tools without asking — set JARVIS_PERMISSION=default to require approval"
                 if perm == "bypass" else "")

    print(f"""
  JARVIS · Live HUD
  ──────────────────────────────────────────────
  brain        {brain}
  workdir      {runtime.WORKDIR}
  permission   {perm}{perm_note}
  voice        {vo}
  open         http://localhost:{PORT}
  web          {("https://" + PUBLIC_HOST + "  (password " + ("set" if remote_auth.password_set() else "NOT SET — run python3 set_password.py") + ", read + draft only)") if PUBLIC_HOST else "off (local only)"}
""", flush=True)

    srv = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    brief.start_background()                  # unread-email count for the TODAY panel
    voice.warm_local()                        # backup ears (on-device Whisper) ready before they are needed
    if kind == "claude" and os.environ.get("JARVIS_WARM", "1") != "0":
        # pre-warm the core so even the first question skips the cold start
        import threading
        threading.Thread(target=runtime.CORE.prewarm, args=(persona(),), daemon=True).start()
    if os.environ.get("JARVIS_OPEN", "1") != "0":
        webbrowser.open(f"http://localhost:{PORT}")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\n  down.")
        srv.shutdown()


if __name__ == "__main__":
    main()
