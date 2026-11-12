"""
Set (or change) the password for reaching JARVIS over the web.

    python3 set_password.py

You type it here, on your Mac; only a salted hash is saved to .env. Changing it
signs out every browser that was logged in. Restart JARVIS afterwards.
"""
import getpass
import os
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import remote_auth  # noqa: E402

ENV = pathlib.Path(__file__).resolve().parent / ".env"

pw = getpass.getpass("New JARVIS web password (at least 12 characters): ")
if len(pw) < 12:
    sys.exit("Too short — use at least 12 characters (a few random words works well).")
if getpass.getpass("Type it again: ") != pw:
    sys.exit("They didn't match. Nothing changed.")

line = f"JARVIS_PASSWORD_HASH={remote_auth.hash_password(pw)}"
text = ENV.read_text() if ENV.exists() else ""
lines = [l for l in text.splitlines() if not l.startswith("JARVIS_PASSWORD_HASH=")]
lines += ["", "# Web password (hash only — set with: python3 set_password.py)", line]
ENV.write_text("\n".join(lines).strip() + "\n")
os.chmod(ENV, 0o600)
remote_auth.revoke_all()
print("Password saved. All web sessions signed out. Restart JARVIS (./jarvis.sh) to use it.")
