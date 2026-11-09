#!/usr/bin/env bash
# Build and (optionally) upload foreman-plane.
#
# Credentials are read from the environment only — no token ever appears in
# this file.  Tests always run before anything is built.
set -euo pipefail

cd "$(dirname "$0")/.."

PYTHON="${PYTHON:-python3}"
export PYTHONPATH="${PYTHONPATH:-}:$PWD/src"

echo "==> running the test suite"
"$PYTHON" -m unittest discover -s tests -v

echo "==> cleaning previous artefacts"
rm -rf build dist ./*.egg-info

echo "==> building"
if "$PYTHON" -c "import build" >/dev/null 2>&1; then
  "$PYTHON" -m build
elif "$PYTHON" -m pip --version >/dev/null 2>&1; then
  "$PYTHON" -m pip wheel . --no-deps --wheel-dir dist
  "$PYTHON" -m pip install --quiet build
  "$PYTHON" -m build --sdist
else
  echo "no build backend available (need 'build' or 'pip')" >&2
  exit 1
fi

echo "==> artefacts"
ls -l dist

if [ -n "${TWINE_USERNAME:-}" ] && [ -n "${TWINE_PASSWORD:-}" ]; then
  echo "==> uploading (credentials taken from the environment)"
  if "$PYTHON" -c "import twine" >/dev/null 2>&1; then
    "$PYTHON" -m twine upload dist/*
  else
    "$PYTHON" -m pip install --quiet twine
    "$PYTHON" -m twine upload dist/*
  fi
else
  echo "==> TWINE_USERNAME/TWINE_PASSWORD are not set — skipping the upload"
fi
