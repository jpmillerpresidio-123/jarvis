# JARVIS — a voice assistant for your Mac, powered by Claude Code

Say **"Hey JARVIS"** and ask anything. It answers out loud, can check your
calendar and email, look things up, make Google Docs and Gmail drafts, and shows
everything on a dashboard.

Built on [jarvis-os](https://github.com/Itsme23476/jarvis-os-public) (MIT), with:

- **Free, unlimited voice.** Kokoro runs on your Mac's GPU, with no account and no credits.
- **Free hearing.** Whisper speech-to-text runs on the Mac too.
- **"Hey JARVIS" in the background**, plus a double-tap-⌘ pop-up bar.
- **Interrupt it.** Say "JARVIS" while it's talking and it stops.
- **"Go to bed" / "go on standby"** by voice, or the ☾ button on the dashboard.
- **A TODAY panel** showing your next calendar event, unread email, your teams' games, and (optional) Blackbaud assignments.
- **A real Mac app**: JARVIS.app in your Applications folder.

## What you need

| | |
|---|---|
| **A Mac with Apple Silicon** | M1 or newer |
| **Claude Code** | installed and signed in with a Claude subscription. This is the brain. Install from [claude.com/claude-code](https://claude.com/claude-code), then run `claude` once and sign in. |
| **Apple's command line tools** | `xcode-select --install` |
| **About 2 GB of free space** | for the voice and hearing models |

Nothing else is paid. ElevenLabs is optional.

For email and calendar, connect Gmail and Google Calendar to your Claude account
(claude.ai → Settings → Connectors). JARVIS uses whatever you've connected.

## Install

```bash
git clone https://github.com/jpmillerpresidio-123/jarvis.git jarvis
cd jarvis
./setup.sh
```

Then:

1. Open `.env` and set `JARVIS_USER_NAME` to your first name. Optionally set your sports teams.
2. Open **JARVIS** from your Applications folder (or Spotlight).
3. Allow the **microphone** when macOS asks.
4. For the double-tap-⌘ shortcut, allow JARVIS under System Settings → Privacy & Security → **Accessibility**.
5. Say **"Hey JARVIS"**.

The first question downloads the hearing model (about 480 MB), so give it a minute.

## Using it

| Say / do | What happens |
|---|---|
| "Hey JARVIS, …" | asks a question; it answers out loud |
| double-tap **⌘** | pop-up bar: talk, then tap ⌘ once when you're done, or type |
| "JARVIS" while it's talking | stops it mid-sentence and listens |
| "Go to bed" / "go on standby" | goes quiet until the next "Hey JARVIS" |
| ☾ **Go on standby** button | same thing, from the dashboard |

## Settings (`.env`)

| Setting | What it does |
|---|---|
| `JARVIS_USER_NAME` | your name |
| `JARVIS_TEAMS` | ESPN teams, e.g. `basketball/nba/gs,football/nfl/sf` |
| `JARVIS_KOKORO_VOICE` | `bm_george` (default), `bm_lewis`, `bm_daniel`, `bm_fable`, `am_michael`, `am_fenrir`, … |
| `JARVIS_TTS` | `kokoro` (free) or `elevenlabs` (paid, needs `ELEVENLABS_API_KEY`) |
| `JARVIS_HOTKEY` | the double-tap key: `cmd`, `ctrl`, `option`, `shift`, `fn` |
| `JARVIS_PERMISSION` | `bypass` lets JARVIS use tools without asking (needed for email/calendar to work hands-free) |

After changing `.env`, quit JARVIS (menu → **Quit JARVIS & Stop Assistant**) and open it again.

### Optional: school assignments (Blackbaud)

Copy `tools/.env.local.example` to `tools/.env.local` and paste your Blackbaud
calendar feed link into `BLACKBAUD_ICAL`. **That link works like a password**:
don't share it or commit it. It's ignored by git.

## Privacy and safety

- Everything runs on your Mac. The dashboard is only at `http://localhost:8730`
  and refuses connections from other computers.
- Voice and hearing stay on the Mac (unless you switch to ElevenLabs).
- In `bypass` mode, JARVIS can use your connected accounts and run commands
  without asking. It's told never to send email, delete things or spend money
  unless you ask for exactly that, but it's still an AI with access to your
  stuff. Don't expose it to the internet.
- Your `.env`, `tools/.env.local`, logs and models are in `.gitignore` and never
  get committed.

## Troubleshooting

- **Doesn't respond to "Hey JARVIS":** check System Settings → Privacy & Security → Microphone → JARVIS is on.
- **Log file:** `~/Library/Logs/JARVIS.log`
- **Moved the folder?** Run `./make_app.sh` again.

## License

MIT. See [LICENSE](LICENSE). Original project © SoftwareGent.
