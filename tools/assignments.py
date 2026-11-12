"""Your Blackbaud assignments, straight from the school's iCal feed.

    python3 assignments.py                 upcoming 14 days + anything due in the last 3
    python3 assignments.py --days 7        a different window
    python3 assignments.py --class spanish only one class (any part of its name)
    python3 assignments.py --full "essay"  full instructions for matching assignments
    python3 assignments.py --json          machine-readable

The feed link lives in .env.local (BLACKBAUD_ICAL) next to this file — it works
like a password, so never print or read it out. Stdlib only, ~0.6s, cached 5 min.

The feed has class, title, due time and the teacher's instructions, but NOT
whether you marked an assignment Completed — say "check Blackbaud for status"
rather than calling something overdue for certain.
"""
import argparse
import datetime as dt
import html
import json
import os
import re
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
CACHE = Path(tempfile.gettempdir()) / "jarvis-assignments.ics"
CACHE_S = 300


def feed_url():
    env = HERE / ".env.local"
    for line in (env.read_text().splitlines() if env.exists() else []):
        if line.startswith("BLACKBAUD_ICAL="):
            return line.split("=", 1)[1].strip().replace("webcal://", "https://", 1)
    sys.exit("No BLACKBAUD_ICAL in tools/.env.local")


def fetch(fresh=False):
    if not fresh and CACHE.exists() and time.time() - CACHE.stat().st_mtime < CACHE_S:
        return CACHE.read_text(encoding="utf-8")
    req = urllib.request.Request(feed_url(), headers={"User-Agent": "jarvis-assignments/1.0"})
    with urllib.request.urlopen(req, timeout=20) as r:
        text = r.read().decode("utf-8", "replace")
    CACHE.write_text(text, encoding="utf-8")
    os.chmod(CACHE, 0o600)
    return text


def _date(v):
    v = v.strip()
    if re.fullmatch(r"\d{8}", v):
        return dt.datetime.strptime(v, "%Y%m%d"), True
    v = v.rstrip("Z")
    return dt.datetime.strptime(v[:15], "%Y%m%dT%H%M%S"), False


def parse(text):
    text = re.sub(r"\r?\n[ \t]", "", text)                 # unfold continued lines
    out = []
    for block in re.findall(r"BEGIN:VEVENT(.*?)END:VEVENT", text, re.S):
        f = {}
        for line in block.strip().splitlines():
            if ":" not in line:
                continue
            key, val = line.split(":", 1)
            f[key.split(";")[0]] = val
        summary = html.unescape(f.get("SUMMARY", "")).replace("\\,", ",").strip()
        cls, _, title = summary.partition(": ")
        if not title:
            cls, title = "", summary
        due_raw = f.get("DTEND") or f.get("DTSTART")
        if not due_raw:
            continue
        due, all_day = _date(due_raw)
        if all_day and f.get("DTEND"):
            # all-day events run assigned-date → day AFTER the due date (iCal's
            # end date is exclusive), so the real due date is one day earlier
            due -= dt.timedelta(days=1)
        desc = html.unescape(f.get("DESCRIPTION", "")).replace("\\n", "\n").replace("\\,", ",")
        desc = re.sub(r"<[^>]+>", " ", desc)
        desc = re.sub(r"[ \t]+", " ", desc).strip()
        out.append(dict(cls=cls.strip(), title=title.strip(), due=due, all_day=all_day, details=desc))
    return sorted(out, key=lambda a: a["due"])


def when(a, now):
    d = (a["due"].date() - now.date()).days
    day = {0: "today", 1: "tomorrow", -1: "yesterday"}.get(d, a["due"].strftime("%a %b %-d"))
    return day if a["all_day"] else f"{day} {a['due'].strftime('%-I:%M %p')}"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=14)
    ap.add_argument("--past", type=int, default=3)
    ap.add_argument("--class", dest="cls", default="")
    ap.add_argument("--full", default="", help="show full instructions for titles matching this")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--fresh", action="store_true", help="skip the 5-minute cache")
    a = ap.parse_args()

    now = dt.datetime.now()
    today = dt.datetime.combine(now.date(), dt.time())
    items = [x for x in parse(fetch(a.fresh))
             if today - dt.timedelta(days=a.past) <= x["due"] <= today + dt.timedelta(days=a.days)
             and a.cls.lower() in x["cls"].lower()
             and a.full.lower() in (x["title"] + " " + x["cls"]).lower()]

    if a.json:
        print(json.dumps([dict(x, due=x["due"].isoformat()) for x in items], ensure_ascii=False, indent=1))
        return
    if not items:
        print("No assignments in that window.")
        return
    for x in items:
        past = " (past due — may already be done)" if x["due"].date() < now.date() else ""
        print(f"- {when(x, now)} · {x['cls'] or 'No class'}: {x['title']}{past}")
        if x["details"]:
            print("    " + (x["details"] if a.full else x["details"][:160] + ("…" if len(x["details"]) > 160 else "")))
    print(f"\n({len(items)} assignments · feed has no completed/to-do status)")


if __name__ == "__main__":
    main()
