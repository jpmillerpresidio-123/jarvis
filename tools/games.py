"""Your teams (JARVIS_TEAMS in .env): last result, next game (date, time, opponent, TV) and record.

    python3 games.py              all your teams
    python3 games.py lakers       one team (any part of its name)

Same ESPN data as the dashboard's TODAY panel — faster and more reliable than a
web search for "when do we play next" or "did we win".
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import brief  # noqa: E402

want = " ".join(sys.argv[1:]).lower()
for t in brief.games() if isinstance(brief.games(), list) else []:
    if want and want not in t["name"].lower():
        continue
    print(f"{t['name']} ({t['record']})")
    n, l = t["next"], t["last"]
    if n:
        live = " — LIVE now: " + f"{n['us_score']}-{n['them_score']} {n['detail']}" if n["state"] == "in" else ""
        print(f"  next: {'TODAY ' if n['today'] else ''}{n['local']} {n['vs']} ({n['opponent']})"
              f"{' · TV: ' + n['tv'] if n['tv'] else ''}{live}")
    if l:
        print(f"  last: {'W' if l['won'] else 'L'} {l['us_score']}-{l['them_score']} {l['vs']} ({l['local']})")
