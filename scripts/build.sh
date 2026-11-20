#!/bin/sh
set -eu
cd "$(dirname "$0")/.."
python -m pip install '.[build]'
exec python -m PyInstaller --noconfirm markcontrol.spec
