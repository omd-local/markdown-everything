#!/usr/bin/env bash
# Manual sign + notarize + staple for a locally-built omd.app.
#
# Use this when you build the .app on your laptop (no CI) and want to ship
# the DMG by hand. Mirrors release.yml's signing path so the local DMG is
# byte-for-byte equivalent to what CI produces.
#
# Prereqs (one-time, see ../stage2-templates/README.md):
#   1. Developer ID Application cert installed in login keychain
#   2. notarytool keychain profile stored:
#        xcrun notarytool store-credentials omd-notary \
#          --key ~/path/to/AuthKey_<KEYID>.p8 \
#          --key-id <KEYID> \
#          --issuer <ISSUERID>
#   3. create-dmg installed: brew install create-dmg
#
# Usage:
#   ./local-sign.sh /path/to/built/omd.app
#
# Reads APPLE_TEAM_ID from environment OR from the first signing identity
# matching "Developer ID Application" in the login keychain.

set -euo pipefail

APP_PATH="${1:-}"
if [ -z "$APP_PATH" ] || [ ! -d "$APP_PATH" ]; then
  echo "Usage: $0 /path/to/omd.app" >&2
  exit 1
fi

APP_NAME=$(basename "$APP_PATH" .app)
ENTITLEMENTS="$(dirname "$0")/entitlements.plist"
NOTARY_PROFILE="${NOTARY_PROFILE:-omd-notary}"
OUTPUT_DIR="${OUTPUT_DIR:-$HOME/Desktop}"

# Discover signing identity if not provided
if [ -z "${APPLE_TEAM_ID:-}" ]; then
  APPLE_TEAM_ID=$(security find-identity -v -p codesigning \
    | grep "Developer ID Application" \
    | head -1 \
    | sed -E 's/.*\(([A-Z0-9]{10})\).*/\1/')
fi
if [ -z "$APPLE_TEAM_ID" ]; then
  echo "ERROR: no 'Developer ID Application' cert in login keychain." >&2
  echo "Install one via developer.apple.com/account first (see README)." >&2
  exit 2
fi
echo "→ Using Team ID: $APPLE_TEAM_ID"

# ---- 1. Sign ---------------------------------------------------------------
echo "→ Codesign $APP_PATH"
codesign --force --deep --options runtime --timestamp \
  --sign "Developer ID Application: $APPLE_TEAM_ID" \
  ${ENTITLEMENTS:+--entitlements "$ENTITLEMENTS"} \
  "$APP_PATH"

codesign --verify --deep --strict --verbose=2 "$APP_PATH"

# ---- 2. Build DMG ----------------------------------------------------------
DMG_PATH="$OUTPUT_DIR/$APP_NAME.dmg"
[ -f "$DMG_PATH" ] && rm "$DMG_PATH"
echo "→ Build DMG: $DMG_PATH"
create-dmg \
  --volname "$APP_NAME" \
  --app-drop-link 425 120 \
  --window-size 700 400 \
  --icon-size 100 \
  --icon "$APP_NAME.app" 175 120 \
  "$DMG_PATH" \
  "$APP_PATH"

# ---- 3. Notarize -----------------------------------------------------------
echo "→ Submit to Apple notary (waits up to 30 min)"
xcrun notarytool submit "$DMG_PATH" \
  --keychain-profile "$NOTARY_PROFILE" \
  --wait \
  --timeout 30m

# ---- 4. Staple -------------------------------------------------------------
echo "→ Staple ticket"
xcrun stapler staple "$DMG_PATH"
xcrun stapler validate "$DMG_PATH"

echo ""
echo "✓ Done: $DMG_PATH"
echo "  Test on a fresh Mac (or one that's never seen this DMG):"
echo "    xattr -d com.apple.quarantine $DMG_PATH  # bypass Gatekeeper for self-test"
echo "    open $DMG_PATH"
