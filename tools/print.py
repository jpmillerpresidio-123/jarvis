"""Print to your home printer — only when it's actually reachable.

    python3 print.py status                     "home" (printer visible on Wi-Fi) or "away"
    python3 print.py file  <path>               print a PDF / text / HTML file
    python3 print.py text  "Title" < body.txt   print plain text with a title
    (add --dry-run to check everything without printing)

The printer is found over Bonjour, so "home" means "on the same Wi-Fi as the HP".
No location permission needed. Exit code 0 = printed (or would print), 3 = away.
"""
import os
import subprocess
import sys
import tempfile
import time

# Set these in .env. JARVIS_PRINTER = the queue name from `lpstat -p`;
# JARVIS_PRINTER_MATCH = part of the printer's name as it shows on Wi-Fi.
PRINTER = os.environ.get("JARVIS_PRINTER", "")
NEEDLE = os.environ.get("JARVIS_PRINTER_MATCH", "")


def reachable(timeout=3.0):
    """Browse Bonjour for the printer for a few seconds."""
    for svc in ("_ipps._tcp", "_ipp._tcp"):
        p = subprocess.Popen(["dns-sd", "-B", svc, "local."], stdout=subprocess.PIPE,
                             stderr=subprocess.DEVNULL, text=True)
        t0, seen = time.time(), False
        try:
            os.set_blocking(p.stdout.fileno(), False)
            buf = ""
            while time.time() - t0 < timeout:
                try:
                    chunk = p.stdout.read() or ""
                except (BlockingIOError, TypeError):
                    chunk = ""
                buf += chunk
                if NEEDLE.lower() in buf.lower():
                    seen = True
                    break
                time.sleep(0.15)
        finally:
            p.kill()
        if seen:
            return True
    return False


def lp(path, title, dry):
    if dry:
        print(f"(dry run) would print {path!r} on {PRINTER} as {title!r}")
        return 0
    r = subprocess.run(["lp", "-d", PRINTER, "-t", title, path], capture_output=True, text=True)
    print((r.stdout or r.stderr).strip() or "sent")
    return r.returncode


def main():
    args = [a for a in sys.argv[1:] if a != "--dry-run"]
    dry = "--dry-run" in sys.argv
    if not args:
        sys.exit(__doc__)
    if not PRINTER or not NEEDLE:
        print("No printer set up: add JARVIS_PRINTER and JARVIS_PRINTER_MATCH to .env. Nothing was printed.")
        return 3
    cmd = args[0]
    home = reachable()
    if cmd == "status":
        print("home" if home else "away")
        return 0 if home else 3
    if not home:
        print("AWAY: the home printer isn't on this Wi-Fi. Nothing was printed.")
        return 3
    if cmd == "file" and len(args) > 1:
        return lp(args[1], os.path.basename(args[1]), dry)
    if cmd == "text":
        title = args[1] if len(args) > 1 else "JARVIS"
        body = sys.stdin.read()
        with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False, encoding="utf-8") as f:
            f.write(f"{title}\n{'=' * len(title)}\n\n{body.strip()}\n")
        return lp(f.name, title, dry)
    sys.exit(__doc__)


if __name__ == "__main__":
    sys.exit(main())
