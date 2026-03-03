#!/usr/bin/env bash
set -euo pipefail

REPO_URL="https://github.com/fresh-fx59/iron-lady-assistant.git"
INSTALL_DIR="${INSTALL_DIR:-$HOME/iron-lady-assistant}"
DEFAULT_MODEL="${DEFAULT_MODEL:-sonnet}"
BOT_TOKEN="${BOT_TOKEN:-}"
ALLOWED_USER_IDS="${ALLOWED_USER_IDS:-}"
SETUP_SERVICE=1
NON_INTERACTIVE=0

usage() {
  cat <<USAGE
One-line installer for Iron Lady Assistant.

Usage:
  bash install.sh [options]

Options:
  --dir <path>                 Install directory (default: ~/iron-lady-assistant)
  --repo <url>                 Git repo URL
  --bot-token <token>          Telegram bot token
  --allowed-user-ids <ids>     Comma-separated Telegram user IDs
  --model <sonnet|opus|haiku>  Default model (default: sonnet)
  --no-service                 Do not install/start systemd service
  --non-interactive            Fail instead of prompting for missing values
  -h, --help                   Show help
USAGE
}

while [ $# -gt 0 ]; do
  case "$1" in
    --dir) INSTALL_DIR="$2"; shift 2 ;;
    --repo) REPO_URL="$2"; shift 2 ;;
    --bot-token) BOT_TOKEN="$2"; shift 2 ;;
    --allowed-user-ids) ALLOWED_USER_IDS="$2"; shift 2 ;;
    --model) DEFAULT_MODEL="$2"; shift 2 ;;
    --no-service) SETUP_SERVICE=0; shift ;;
    --non-interactive) NON_INTERACTIVE=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown option: $1"; usage; exit 1 ;;
  esac
done

need_cmd() {
  command -v "$1" >/dev/null 2>&1
}

install_npm_global() {
  local pkg="$1"
  local global_root
  global_root="$(npm root -g 2>/dev/null || true)"

  # If global npm dir is writable, install as current user; otherwise use sudo.
  if [ -n "$global_root" ] && [ -w "$global_root" ]; then
    npm install -g "$pkg"
    return
  fi

  if need_cmd sudo; then
    sudo npm install -g "$pkg"
    return
  fi

  echo "ERROR: failed to install $pkg globally via npm (no sudo fallback)." >&2
  return
}

apt_install() {
  local pkgs=("$@")
  if ! need_cmd sudo; then
    echo "ERROR: sudo is required to install packages." >&2
    exit 1
  fi
  sudo apt-get update -y
  sudo apt-get install -y "${pkgs[@]}"
}

echo "[1/7] Installing OS dependencies..."
if ! need_cmd git || ! need_cmd curl || ! need_cmd python3 || ! need_cmd ffmpeg; then
  apt_install git curl ca-certificates ffmpeg python3 python3-venv python3-pip
fi

if ! need_cmd npm; then
  echo "Installing Node.js 20..."
  if ! need_cmd sudo; then
    echo "ERROR: sudo is required to install Node.js." >&2
    exit 1
  fi
  curl -fsSL https://deb.nodesource.com/setup_20.x | sudo -E bash -
  sudo apt-get install -y nodejs
fi

echo "[2/7] Installing agent CLIs (Claude + Codex)..."
if ! need_cmd claude; then
  install_npm_global "@anthropic-ai/claude-code"
fi
if ! need_cmd codex; then
  install_npm_global "@openai/codex"
fi

echo "[3/7] Cloning/updating repository..."
if [ -d "$INSTALL_DIR/.git" ]; then
  git -C "$INSTALL_DIR" fetch --all --prune
  git -C "$INSTALL_DIR" checkout main
  git -C "$INSTALL_DIR" pull --ff-only
else
  git clone "$REPO_URL" "$INSTALL_DIR"
fi

cd "$INSTALL_DIR"

echo "[4/7] Installing Python dependencies..."
if [ ! -d venv ]; then
  python3 -m venv venv
fi
venv/bin/pip install --upgrade pip >/dev/null
venv/bin/pip install -r requirements.txt >/dev/null

if [ "$NON_INTERACTIVE" -eq 1 ]; then
  if [ -z "$BOT_TOKEN" ] || [ -z "$ALLOWED_USER_IDS" ]; then
    echo "ERROR: --non-interactive requires --bot-token and --allowed-user-ids." >&2
    exit 1
  fi
else
  if [ -z "$BOT_TOKEN" ]; then
    read -rp "Telegram bot token (from @BotFather): " BOT_TOKEN
  fi
  if [ -z "$ALLOWED_USER_IDS" ]; then
    read -rp "Allowed Telegram user IDs (comma-separated): " ALLOWED_USER_IDS
  fi
fi

if [ -z "$BOT_TOKEN" ] || [ -z "$ALLOWED_USER_IDS" ]; then
  echo "ERROR: BOT_TOKEN and ALLOWED_USER_IDS are required." >&2
  exit 1
fi

echo "[5/7] Writing .env..."
if [ -f .env ]; then
  cp .env ".env.backup.$(date +%s)"
fi

cat > .env <<ENV
TELEGRAM_BOT_TOKEN=$BOT_TOKEN
ALLOWED_USER_IDS=$ALLOWED_USER_IDS
DEFAULT_PROVIDER=codex
DEFAULT_MODEL=$DEFAULT_MODEL
CLAUDE_WORKING_DIR=$HOME
IDLE_TIMEOUT=120
TELEGRAM_REQUEST_TIMEOUT_SECONDS=90
TELEGRAM_POLLING_TIMEOUT_SECONDS=30
TELEGRAM_BACKOFF_MIN_SECONDS=1.0
TELEGRAM_BACKOFF_MAX_SECONDS=30.0
TELEGRAM_BACKOFF_FACTOR=1.5
TELEGRAM_BACKOFF_JITTER=0.1
PROGRESS_DEBOUNCE_SECONDS=3.0
METRICS_PORT=9101
MEMORY_DIR=memory
TOOLS_DIR=tools
AUTONOMY_ENABLED=1
AUTONOMY_FAILURE_THRESHOLD=3
AUTONOMY_FAILURE_WINDOW_MINUTES=60
AUTONOMY_ALERT_COOLDOWN_MINUTES=30
ENV

echo "[6/7] Verifying runtime..."
venv/bin/python3 -c "from src.config import VERSION; print('Smoke test OK: v'+VERSION)"

if [ "$SETUP_SERVICE" -eq 1 ]; then
  echo "[7/7] Installing systemd service..."
  if ! need_cmd sudo; then
    echo "ERROR: sudo is required for service install. Re-run with --no-service if needed." >&2
    exit 1
  fi

  cat > /tmp/telegram-bot.service <<SERVICE
[Unit]
Description=Telegram Claude Code Bot
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=$(whoami)
WorkingDirectory=$INSTALL_DIR
ExecStart=$INSTALL_DIR/run.sh
Restart=always
RestartSec=5
StartLimitBurst=5
StartLimitIntervalSec=600
EnvironmentFile=$INSTALL_DIR/.env
Environment=PATH=$HOME/.local/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
NoNewPrivileges=true
ProtectSystem=strict
ReadWritePaths=$HOME
PrivateTmp=true

[Install]
WantedBy=multi-user.target
SERVICE

  sudo mv /tmp/telegram-bot.service /etc/systemd/system/telegram-bot.service
  sudo systemctl daemon-reload
  sudo systemctl enable --now telegram-bot.service
  sudo systemctl --no-pager --full status telegram-bot.service | head -n 20 || true
else
  echo "[7/7] Skipping service install (--no-service)."
fi

echo
echo "Install complete."
echo "Run manually: cd $INSTALL_DIR && ./run.sh"
echo "One-line installer command for docs:"
echo "bash <(curl -fsSL https://raw.githubusercontent.com/fresh-fx59/iron-lady-assistant/main/install.sh)"
