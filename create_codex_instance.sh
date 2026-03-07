#!/bin/bash
set -euo pipefail

if [ $# -lt 1 ]; then
  echo "Usage: $0 <instance_name> [install_path]"
  echo "Example: $0 codex3"
  exit 1
fi

INSTANCE_NAME="$1"
INSTALL_PATH="${2:-/usr/local/bin/$INSTANCE_NAME}"
TARGET_DIR="$HOME/.$INSTANCE_NAME"
CODEX_BIN="$(command -v codex || true)"

if [ -z "$CODEX_BIN" ]; then
  echo "codex executable not found in PATH."
  exit 1
fi

if [ -d "$TARGET_DIR" ]; then
  echo "Directory $TARGET_DIR already exists."
else
  echo "Creating directory $TARGET_DIR..."
  mkdir -p "$TARGET_DIR"
fi

if [ -f "$HOME/.gitconfig" ]; then
  echo "Symlinking .gitconfig..."
  ln -sf "$HOME/.gitconfig" "$TARGET_DIR/.gitconfig"
fi

if [ -d "$HOME/.ssh" ]; then
  echo "Symlinking .ssh..."
  ln -sf "$HOME/.ssh" "$TARGET_DIR/.ssh"
fi

echo "Installing wrapper to $INSTALL_PATH..."
sudo tee "$INSTALL_PATH" >/dev/null <<EOF
#!/bin/sh
export HOME="$TARGET_DIR"
exec "$CODEX_BIN" "\$@"
EOF
sudo chmod +x "$INSTALL_PATH"

echo "Setup complete for $INSTANCE_NAME."
echo "Wrapper installed at $INSTALL_PATH"
echo "Authenticate with: HOME=$TARGET_DIR codex login"
