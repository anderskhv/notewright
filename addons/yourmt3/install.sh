#!/usr/bin/env bash
# Installs the optional YourMT3+ add-on: its own Python 3.12 environment, its code and
# one checkpoint (~600 MB) from Hugging Face. Run from anywhere:
#   addons/yourmt3/install.sh
set -euo pipefail
cd "$(dirname "$0")"
# Its libraries are pinned to versions that predate Python 3.13, so it gets its own 3.12.
export PATH="$HOME/.local/bin:$PATH"
if command -v uv >/dev/null; then  # uv fetches Python 3.12 itself and installs much faster
  [ -x venv/bin/python ] || uv venv -q --python 3.12 venv
  TORCH=""  # a plain string: macOS's bash 3.2 treats an empty array as unset
  if [ "$(uname -s)" = Linux ]; then TORCH="--torch-backend=auto"; fi
  uv pip install -q --python venv/bin/python $TORCH torch torchaudio
  uv pip install -q --python venv/bin/python $TORCH -r requirements.txt
else
  PY="${PYTHON:-python3.12}"
  if ! command -v "$PY" >/dev/null; then
    echo "The add-on needs Python 3.12. Easiest: run Notewright's install.sh, or on a Mac: brew install python@3.12"
    exit 1
  fi
  [ -x venv/bin/python ] || "$PY" -m venv venv
  venv/bin/pip install --upgrade pip
  venv/bin/pip install torch torchaudio
  venv/bin/pip install -r requirements.txt
fi
venv/bin/python download.py
echo "YourMT3+ add-on installed. Restart Notewright; full-band transcriptions now use it for the piano."
