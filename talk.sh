#!/bin/bash
# Bind this to a keyboard shortcut (Shortcuts app → Run Shell Script).
#   talk.sh        start listening now, no wake word
#   talk.sh stop   stop JARVIS talking mid-answer
if [ "$1" = "stop" ]; then sig=USR2; else sig=USR1; fi
pkill -"$sig" -f "listener.py" || osascript -e 'display notification "The listener isn'"'"'t running. Start it with ./jarvis.sh" with title "JARVIS"'
