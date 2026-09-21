#!/bin/zsh
# Remove the per-user macOS launchd agent without touching user data.

set -u

LABEL="com.kindle-to-anki"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
DOMAIN="gui/$(/usr/bin/id -u)"

/bin/launchctl bootout "$DOMAIN/$LABEL" 2>/dev/null || true
/bin/rm -f "$PLIST"

print "Removed launchd agent and $PLIST"
print "Checkpoint, Keychain entry, and logs were left untouched."
