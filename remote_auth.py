"""
Password + session cookies for reaching JARVIS over the web (through a tunnel).

Requests from this Mac itself (the pop-up, "Hey JARVIS", http://localhost) never
need a password. Anything that arrives through the tunnel does.

  · The password is never stored — only a salted PBKDF2 hash in .env
    (JARVIS_PASSWORD_HASH), set with:  python3 set_password.py
  · Sessions are random 256-bit tokens in an HttpOnly, Secure, SameSite=Strict
    cookie; only their SHA-256 is kept on disk (.sessions.json, owner-only).
  · Wrong passwords: 5 per address in 15 minutes, or 30 total in an hour, locks
    logins out for 15 minutes.
"""
import hashlib
import hmac
import json
import os
import pathlib
import secrets
import threading
import time

ROOT = pathlib.Path(__file__).resolve().parent
SESS_FILE = ROOT / ".sessions.json"
COOKIE = "jarvis_session"
SESSION_DAYS = 30
_ITER = 310_000
_lock = threading.Lock()
_fails = {}            # ip -> [timestamps]
_all_fails = []        # timestamps
_locked_until = {"all": 0.0}


def hash_password(pw):
    salt = secrets.token_bytes(16)
    dk = hashlib.pbkdf2_hmac("sha256", pw.encode(), salt, _ITER)
    return f"pbkdf2${_ITER}${salt.hex()}${dk.hex()}"


def password_set():
    return os.environ.get("JARVIS_PASSWORD_HASH", "").startswith("pbkdf2$")


def _check(pw):
    try:
        _, it, salt, want = os.environ.get("JARVIS_PASSWORD_HASH", "").split("$")
        dk = hashlib.pbkdf2_hmac("sha256", pw.encode(), bytes.fromhex(salt), int(it))
        return hmac.compare_digest(dk.hex(), want)
    except (ValueError, TypeError):
        return False


def login(pw, ip):
    """Returns (token | None, error | None)."""
    now = time.time()
    with _lock:
        if not password_set():
            return None, "No password set yet. On the Mac, run: python3 set_password.py"
        if now < _locked_until["all"] or now < _locked_until.get(ip, 0):
            return None, "Too many wrong tries. Wait 15 minutes."
    ok = _check(pw or "")
    with _lock:
        if not ok:
            _fails[ip] = [t for t in _fails.get(ip, []) if now - t < 900] + [now]
            _all_fails[:] = [t for t in _all_fails if now - t < 3600] + [now]
            if len(_fails[ip]) >= 5:
                _locked_until[ip] = now + 900
            if len(_all_fails) >= 30:
                _locked_until["all"] = now + 900
    if not ok:
        time.sleep(0.8)                             # slow down guessing
        return None, "Wrong password."
    with _lock:
        _fails.pop(ip, None)
        token = secrets.token_urlsafe(32)
        s = _load()
        s[_h(token)] = now + SESSION_DAYS * 86400
        _save(s)
        return token, None


def valid(cookie_header):
    tok = _cookie(cookie_header)
    if not tok:
        return False
    with _lock:
        exp = _load().get(_h(tok))
    return bool(exp and exp > time.time())


def logout(cookie_header):
    tok = _cookie(cookie_header)
    if tok:
        with _lock:
            s = _load()
            s.pop(_h(tok), None)
            _save(s)


def revoke_all():
    with _lock:
        _save({})


def cookie_header(token):
    return (f"{COOKIE}={token}; Path=/; Max-Age={SESSION_DAYS * 86400}; HttpOnly; Secure; SameSite=Strict")


# ── internals ──
def _h(tok):
    return hashlib.sha256(tok.encode()).hexdigest()


def _cookie(header):
    for part in (header or "").split(";"):
        k, _, v = part.strip().partition("=")
        if k == COOKIE and v:
            return v
    return None


def _load():
    try:
        s = json.loads(SESS_FILE.read_text())
        now = time.time()
        return {k: v for k, v in s.items() if v > now}
    except (OSError, ValueError):
        return {}


def _save(s):
    SESS_FILE.write_text(json.dumps(s))
    os.chmod(SESS_FILE, 0o600)
