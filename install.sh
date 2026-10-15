#!/usr/bin/env bash
# Install SteelPanel for the current user (no root needed).
# Re-run after `git pull` to update.
set -euo pipefail
cd "$(dirname "$0")"

VENV="${STEELPANEL_VENV:-$HOME/.local/share/steelpanel/venv}"
BIN="$HOME/.local/bin"

echo "Creating virtual environment in $VENV"
python3 -m venv "$VENV"
"$VENV/bin/pip" install --quiet --upgrade pip
echo "Installing SteelPanel and its dependencies (PySide6 is ~200 MB, this can take a minute)"
"$VENV/bin/pip" install --quiet .

mkdir -p "$BIN" "$HOME/.local/share/applications"
ln -sf "$VENV/bin/steelpanel" "$BIN/steelpanel"
install -m644 packaging/steelpanel.desktop "$HOME/.local/share/applications/steelpanel.desktop"

echo
echo "Installed: $BIN/steelpanel (also in your app launcher as SteelPanel)"
case ":$PATH:" in
  *":$BIN:"*) ;;
  *) echo "Note: $BIN is not on your PATH yet. Open a new terminal or log out and back in." ;;
esac
echo
echo "Next steps:"
echo "  sudo $VENV/bin/steelpanel --install-udev   # once, lets the app reach your mouse"
echo "  steelpanel --list                          # check what is detected"
