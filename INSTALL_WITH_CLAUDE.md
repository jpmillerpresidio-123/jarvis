# Installing JARVIS: steps for Claude Code

This file is for Claude Code (or any coding agent) asked to "install JARVIS on
my computer". Follow it top to bottom. The person you're helping may not be
technical, so explain what you're doing in plain words, ask them only the
questions below, and do everything else yourself.

## 0. Check the computer

Run these and stop with a friendly explanation if one fails:

- `uname -sm` must print `Darwin arm64` (a Mac with an M1 or newer chip).
  JARVIS doesn't work on Intel Macs, Windows or Linux.
- `xcode-select -p` must succeed. If it doesn't, run `xcode-select --install`,
  tell the person to click **Install** in the window that appears, and wait
  for them to say it's finished.
- `claude --version` (or `~/.local/bin/claude --version`) must work. You're
  running inside Claude Code, so it normally does.
- At least 3 GB of free disk space (`df -h ~`).

## 1. Get the code

Clone into `~/jarvis`, unless the person names another folder:

```bash
git clone https://github.com/jpmillerpresidio-123/jarvis.git ~/jarvis
```

If `~/jarvis` already exists and is this repo, `git -C ~/jarvis pull` instead.
Use a short path with no spaces where you can.

## 2. Ask the person (all at once, in one message)

1. Their **first name**. JARVIS calls them by it.
2. Optional: their **sports teams**. Convert them to ESPN ids in the form
   `sport/league/team` (e.g. Warriors → `basketball/nba/gs`,
   49ers → `football/nfl/sf`, Yankees → `baseball/mlb/nyy`). Check an id
   if you're unsure:
   `curl -s https://site.api.espn.com/apis/site/v2/sports/basketball/nba/teams/gs | head -c 200`
3. Optional: a **voice**. Default is `bm_george` (calm, British). Others:
   `bm_lewis`, `bm_daniel`, `bm_fable` (British), `am_michael`, `am_fenrir`,
   `am_adam` (American).

## 3. Run setup

```bash
cd ~/jarvis
./setup.sh --name "<first name>" --teams "<ids, comma-separated, or empty>" --voice "<voice or empty>"
```

It takes a few minutes and downloads about 1 GB: Python packages, the
"Hey JARVIS" wake word, and the Kokoro voice. It creates `.env` and builds
`~/Applications/JARVIS.app`. It's safe to re-run if something fails part way.
Run it with a long timeout, or in the background and wait for it.

## 4. Start it

```bash
open ~/Applications/JARVIS.app
```

Then tell the person, in plain words:

1. A **microphone** permission box will pop up: click **Allow**.
2. For the double-tap-⌘ shortcut: System Settings → Privacy & Security →
   **Accessibility** → turn on **JARVIS**. You can open that page for them with
   `open "x-apple.systempreferences:com.apple.preference.security?Privacy_Accessibility"`.
   They have to flip the switch themselves.
3. The first question takes about a minute, while the hearing model downloads.
4. Say **"Hey JARVIS"** and ask something. "Go to bed" puts it on standby.

Check it's running: `curl -s -H "Host: localhost:8730" http://127.0.0.1:8730/api/status`
should return JSON. The log is at `~/Library/Logs/JARVIS.log`.

## 5. Optional extras (only if they ask)

- **Email and calendar.** They connect Gmail and Google Calendar to their
  Claude account at claude.ai → Settings → Connectors. JARVIS uses whatever is
  connected.
- **School assignments (Blackbaud).** Copy `tools/.env.local.example` to
  `tools/.env.local`, and have them paste their Blackbaud calendar feed link
  after `BLACKBAUD_ICAL=`. That link works like a password: don't print it back,
  don't share it, don't commit it.
- **Printer.** Set `JARVIS_PRINTER` (a queue name from `lpstat -p`) and
  `JARVIS_PRINTER_MATCH` in `.env`.

After changing `.env`, restart JARVIS: menu → **Quit JARVIS & Stop Assistant**,
then open the app again.

## Rules while installing

- Never type passwords for the person, and never ask them to paste passwords
  into the chat.
- Don't commit or upload `.env` or `tools/.env.local`.
- Don't expose JARVIS to the internet (no tunnels, no port forwarding). It can
  use the person's accounts.
- macOS permission prompts are the person's to click.
