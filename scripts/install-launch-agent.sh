#!/bin/zsh
# Install or reload the per-user macOS launchd agent.

set -e

REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"
LABEL="com.kindle-to-anki"
PLIST_DIR="$HOME/Library/LaunchAgents"
TARGET="$PLIST_DIR/$LABEL.plist"

/bin/mkdir -p "$PLIST_DIR"
/usr/bin/sed \
  -e "s|__REPO_DIR__|$REPO_DIR|g" \
  -e "s|__HOME__|$HOME|g" \
  "$REPO_DIR/launchd/$LABEL.plist.template" > "$TARGET"

/bin/launchctl bootout "gui/$(/usr/bin/id -u)/$LABEL" 2>/dev/null || true
/bin/launchctl bootstrap "gui/$(/usr/bin/id -u)" "$TARGET"
/bin/launchctl enable "gui/$(/usr/bin/id -u)/$LABEL"
/bin/launchctl kickstart -k "gui/$(/usr/bin/id -u)/$LABEL"

print "Installed $TARGET"
print "Logs: $HOME/Library/Logs/kindle-to-anki.log"
