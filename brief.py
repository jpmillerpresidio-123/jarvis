"""
The dashboard's TODAY panel: assignments, unread email, and your teams' games.

  · Assignments — tools/assignments.py (the Blackbaud feed), refreshed every 5 min.
  · Unread email — a small, read-only Claude run that may only call Gmail search,
    in the background every 10 min (Gmail lives behind your Claude connector).
  · Games — ESPN's public schedule data (unofficial; it can change), every 15 min.
    Teams come from JARVIS_TEAMS in .env, e.g.
        JARVIS_TEAMS=basketball/nba/min,football/nfl/min
"""
import datetime as dt
import json
import os
import pathlib
import subprocess
import threading
import time
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parent
TEAMS = [t.strip() for t in os.environ.get("JARVIS_TEAMS", "").split(",")
         if t.strip()]
_cache = {}
_lock = threading.Lock()


def _cached(key, ttl, fn):
    now = time.time()
    with _lock:
        hit = _cache.get(key)
    if hit and now - hit[0] < ttl:
        return hit[1]
    try:
        val = fn()
    except Exception as e:                          # noqa: BLE001
        val = {"error": str(e)[:160]} if not hit else hit[1]
    with _lock:
        _cache[key] = (now, val)
    return val


# ── assignments ────────────────────────────────────────────
def assignments():
    def get():
        out = subprocess.run(["python3", str(ROOT / "tools" / "assignments.py"), "--json", "--days", "3",
                              "--past", "0"], capture_output=True, text=True, timeout=30)
        if out.returncode != 0:
            raise RuntimeError((out.stderr or out.stdout or "assignments failed").strip()[-200:])
        items = json.loads(out.stdout or "[]")
        today = dt.date.today()
        days = [today + dt.timedelta(days=1), today + dt.timedelta(days=2)]
        res = []
        for d in days:
            due = [dict(cls=a["cls"].split(" - ")[0], title=a["title"]) for a in items if a["due"][:10] == d.isoformat()]
            res.append(dict(date=d.isoformat(), label="Tomorrow" if d == days[0] else d.strftime("%A"),
                            count=len(due), items=due))
        return res
    return _cached("assign", 300, get)


# your school's Blackbaud assignment page (optional), e.g.
# https://yourschool.myschoolapp.com/app/student?svcid=edu#studentmyday/assignment-center
BLACKBAUD_URL = os.environ.get("JARVIS_BLACKBAUD_URL", "").strip()


# ── unread email (background) ──────────────────────────────
_mail = {"count": None, "all": None, "at": None, "error": None}
_event = {"next": None, "at": None, "error": None}


def _mail_once():
    env = {k: v for k, v in os.environ.items()
           if k not in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_BASE_URL",
                        "CLAUDE_CODE_ENTRYPOINT", "CLAUDECODE", "CLAUDE_CODE_SESSION_ID")}
    wd = ROOT / ".remote-workdir"
    wd.mkdir(exist_ok=True)
    now = dt.datetime.now().astimezone()
    prompt = ("Use ToolSearch to load the Gmail search_threads tool and the Google Calendar list_events tool. "
              "1) Call search_threads with query 'is:unread in:inbox', and again with query 'is:unread'; each "
              "result has resultCountEstimate. 2) Call list_events on the primary calendar for events starting "
              f"after {now.isoformat(timespec='seconds')} (that's now), ordered by start time, a few results; "
              "pick the FIRST one that hasn't ended (skip all-day holidays/birthdays). Reply with ONLY this JSON: "
              '{"inbox": <n>, "all": <n>, "event": {"title": "...", "start": "<ISO with offset, or YYYY-MM-DD if '
              'all-day>", "end": "<ISO or null>", "location": "<or empty>"} or null}')
    cmd = ["claude", "-p", prompt, "--output-format", "json", "--model", "haiku",
           "--permission-mode", "dontAsk", "--allowedTools", "ToolSearch", "mcp__claude_ai_Gmail__search_threads",
           "mcp__claude_ai_Google_Calendar__list_events", "mcp__claude_ai_Google_Calendar__list_calendars"]
    r = subprocess.run(cmd, cwd=str(wd), env=env, capture_output=True, text=True, timeout=150)
    res = json.loads(r.stdout).get("result", "")
    j = json.loads(res[res.index("{"): res.rindex("}") + 1])
    _event.update(next=j.get("event"), at=time.time(), error=None)
    return int(j["inbox"]), int(j.get("all") or 0)


def _mail_loop():
    while True:
        try:
            n, total = _mail_once()
            _mail.update(count=n, all=total, at=time.time(), error=None)
        except Exception as e:                      # noqa: BLE001
            _mail.update(error=str(e)[:120])
            _event.update(error=str(e)[:120])
        time.sleep(600)


def start_background():
    threading.Thread(target=_mail_loop, daemon=True).start()


def mail():
    return dict(_mail)


# ── games ──────────────────────────────────────────────────
def _team(path):
    sport, league, team_id = path.split("/")
    url = f"https://site.api.espn.com/apis/site/v2/sports/{sport}/{league}/teams/{team_id}/schedule"
    with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "jarvis/1.0"}), timeout=10) as r:
        d = json.loads(r.read())
    team = d.get("team", {})
    me = team.get("abbreviation", "")
    now = dt.datetime.now(dt.timezone.utc)
    local_today = dt.datetime.now().date()

    def game(e):
        c = e["competitions"][0]
        when = dt.datetime.fromisoformat(e["date"].replace("Z", "+00:00"))
        us = next((x for x in c["competitors"] if x["team"].get("abbreviation") == me), c["competitors"][0])
        them = next((x for x in c["competitors"] if x is not us), us)
        sc = lambda x: (x.get("score") or {}).get("displayValue") if isinstance(x.get("score"), dict) else x.get("score")
        st = c["status"]["type"]
        return dict(
            when=when.isoformat(), local=when.astimezone().strftime("%a %b %-d · %-I:%M %p"),
            today=when.astimezone().date() == local_today,
            vs=("vs " if us.get("homeAway") == "home" else "@ ") + them["team"].get("abbreviation", "?"),
            opponent=them["team"].get("displayName", ""),
            tv=", ".join(b.get("media", {}).get("shortName", "") for b in c.get("broadcasts", [])[:2] if b.get("media")),
            state=st.get("state"), final=bool(st.get("completed")), detail=st.get("shortDetail", ""),
            us_score=sc(us), them_score=sc(them), won=us.get("winner"))

    games = [game(e) for e in d.get("events", [])]
    done = [g for g in games if g["final"]]
    ahead = [g for g in games if not g["final"]
             and dt.datetime.fromisoformat(g["when"]) > now - dt.timedelta(hours=5)]
    return dict(name=team.get("displayName", path), short=team.get("name") or team.get("shortDisplayName") or "",
                logo=(team.get("logo") or [{}])[0].get("href") if isinstance(team.get("logo"), list) else team.get("logo"),
                record=(team.get("recordSummary") or ""),
                last=done[-1] if done else None, next=ahead[0] if ahead else None)


def games():
    return _cached("games", 900, lambda: [_team(t) for t in TEAMS])


def everything():
    return dict(assignments=assignments(), mail=mail(), event=dict(_event), games=games(), at=time.time())
