#!/usr/bin/env bash
set -euo pipefail

# Rootless local TTS bootstrap for environments where sudo is unavailable.
# Installs espeak-ng and required shared libs under ~/local/tts-espeak and
# creates ~/local/bin/espeak wrapper used by src/tts.py.

BASE_DIR="${HOME}/local/tts-espeak"
BIN_DIR="${HOME}/local/bin"
WORK_DIR="$(mktemp -d)"
trap 'rm -rf "${WORK_DIR}"' EXIT

mkdir -p "${BASE_DIR}" "${BIN_DIR}"
cd "${WORK_DIR}"

packages=(
  espeak-ng
  espeak-ng-data
  libespeak-ng1
  libpcaudio0
  libsonic0
  libasound2t64
  libpulse0
  libpulse-mainloop-glib0
  libsndfile1
  libx11-xcb1
  libasyncns0
  libflac12t64
  libvorbis0a
  libvorbisenc2
  libopus0
  libogg0
  libmpg123-0t64
  libmp3lame0
)

echo "Downloading TTS packages..."
apt-get download "${packages[@]}"

echo "Extracting packages to ${BASE_DIR}..."
for deb in ./*.deb; do
  dpkg-deb -x "${deb}" "${BASE_DIR}"
done

cat > "${BIN_DIR}/espeak" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
BASE="${HOME}/local/tts-espeak"
export LD_LIBRARY_PATH="${BASE}/usr/lib/x86_64-linux-gnu:${BASE}/usr/lib/x86_64-linux-gnu/pulseaudio:${LD_LIBRARY_PATH:-}"
export ESPEAK_DATA_PATH="${BASE}/usr/lib/x86_64-linux-gnu/espeak-ng-data"
exec "${BASE}/usr/bin/espeak-ng" "$@"
EOF
chmod +x "${BIN_DIR}/espeak"

echo "Validating local TTS binary..."
"${BIN_DIR}/espeak" --version | head -n 1
echo "Local TTS ready: ${BIN_DIR}/espeak"
