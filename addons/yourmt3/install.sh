#!/usr/bin/env bash
# Installs the optional YourMT3+ add-on: its own Python 3.12 environment, its code and
# one checkpoint (~600 MB) from Hugging Face. Run from anywhere:
#   addons/yourmt3/install.sh
set -euo pipefail
cd "$(dirname "$0")"
PY="${PYTHON:-python3.12}"
if ! command -v "$PY" >/dev/null; then
  echo "The add-on needs Python 3.12 (its libraries are pinned to versions that predate 3.13)."
  echo "On a Mac: brew install python@3.12"
  exit 1
fi
"$PY" -m venv venv
venv/bin/pip install --upgrade pip
venv/bin/pip install torch torchaudio
venv/bin/pip install -r requirements.txt
venv/bin/python download.py
echo "YourMT3+ add-on installed. Restart Notewright; full-band transcriptions now use it for the piano."
