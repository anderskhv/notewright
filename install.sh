#!/usr/bin/env bash
# Notewright installer for macOS and Linux. Paste into Terminal:
#
#   curl -fsSL https://raw.githubusercontent.com/anderskhv/notewright/main/install.sh | bash
#
# It installs into ~/notewright (set NOTEWRIGHT_DIR to choose another folder), needs no
# Homebrew and no administrator password, and can be run again to update.
# NOTEWRIGHT_YOURMT3=yes or =no answers the add-on question without asking.
set -euo pipefail

DIR="${NOTEWRIGHT_DIR:-$HOME/notewright}"
REPO="https://github.com/anderskhv/notewright"
step() { printf '\n\033[1m%s\033[0m\n' "$*"; }

case "$(uname -s)" in
  Darwin | Linux) ;;
  *) echo "Notewright's installer runs on macOS and Linux."; exit 1 ;;
esac

step "1/5  Python tools (uv)"
export PATH="$HOME/.local/bin:$PATH"
if ! command -v uv >/dev/null; then
  curl -LsSf https://astral.sh/uv/install.sh | sh -s -- --no-modify-path >/dev/null
fi
echo "uv $(uv --version | cut -d' ' -f2)"

step "2/5  Notewright"
if [ -d "$DIR/.git" ]; then
  git -C "$DIR" pull --ff-only -q && echo "Updated $DIR"
elif [ -e "$DIR" ]; then
  echo "$DIR exists but isn't a Notewright download. Move it away, or set NOTEWRIGHT_DIR."; exit 1
elif command -v git >/dev/null && git --version >/dev/null 2>&1; then
  git clone -q "$REPO" "$DIR" && echo "Downloaded to $DIR"
else  # no git (a fresh Mac): the code as a plain download
  mkdir -p "$DIR"
  curl -fsSL "$REPO/archive/refs/heads/main.tar.gz" | tar -xz -C "$DIR" --strip-components 1
  echo "Downloaded to $DIR"
fi
cd "$DIR"

step "3/5  Libraries (a few GB the first time; updates take seconds)"
[ -x .venv/bin/python ] || uv venv -q --python 3.13 .venv
TORCH=()
[ "$(uname -s)" = Linux ] && TORCH=(--torch-backend auto)  # CPU or the right CUDA build
uv pip install -q --python .venv/bin/python "${TORCH[@]}" -r requirements.txt
# ffmpeg and a JavaScript runtime (for YouTube) from Python packages, when the computer
# doesn't have them already.
if ! command -v ffmpeg >/dev/null; then
  uv pip install -q --python .venv/bin/python imageio-ffmpeg
  ln -sf "$(.venv/bin/python -c 'import imageio_ffmpeg; print(imageio_ffmpeg.get_ffmpeg_exe())')" .venv/bin/ffmpeg
fi
if ! command -v deno >/dev/null && ! command -v node >/dev/null && ! command -v bun >/dev/null; then
  uv pip install -q --python .venv/bin/python deno
fi
echo "Libraries ready"

step "4/5  Models (about 650 MB, once)"
.venv/bin/python -m notewright.fetch

step "5/5  Optional: YourMT3+ for better piano in band recordings"
answer="${NOTEWRIGHT_YOURMT3:-}"
if [ -x addons/yourmt3/venv/bin/python ]; then
  answer=yes  # installed before: keep it up to date
elif [ -z "$answer" ] && [ -r /dev/tty ]; then
  printf 'It finds far more piano notes in band recordings, adds about 3 GB and makes\nfull-band songs take longer. Install it? [y/N] '
  read -r answer < /dev/tty || answer=no
fi
case "$answer" in
  [yY]*) addons/yourmt3/install.sh ;;
  *) echo "Skipped. Add it any time with: $DIR/addons/yourmt3/install.sh" ;;
esac

# A double-click launcher: updates Notewright, then opens it in the browser.
LAUNCHER="$DIR/notewright.command"
cat > "$LAUNCHER" <<EOF
#!/bin/bash
# Starts Notewright. Leave this window open while you use it; close it to stop Notewright.
export PATH="\$HOME/.local/bin:\$PATH"
cd "$DIR" || exit 1
if [ -d .git ]; then
  before=\$(git rev-parse HEAD)
  git pull -q --ff-only 2>/dev/null || true
  if [ "\$before" != "\$(git rev-parse HEAD)" ]; then
    echo "Updating Notewright..."
    uv pip install -q --python .venv/bin/python -r requirements.txt
  fi
fi
exec .venv/bin/python -m notewright
EOF
chmod +x "$LAUNCHER"
if [ "$(uname -s)" = Darwin ] && [ -d "$HOME/Desktop" ]; then
  cp "$LAUNCHER" "$HOME/Desktop/Notewright.command"
  where="Double-click Notewright on your Desktop"
else
  where="Run $LAUNCHER"
fi

step "Done"
echo "$where to start it. It opens in your browser."
echo "The Terminal window that comes with it is Notewright running: close it to stop."
