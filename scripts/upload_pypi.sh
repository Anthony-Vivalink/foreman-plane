#!/usr/bin/env bash
# Upload the built distributions to PyPI with a token kept in a file.
#
# The token never appears in the command line, in the shell history or in
# the chat: it lives in a 0600 file that this script shreds afterwards.
#
#   echo -n 'pypi-xxxxxxxx' > /root/.pypitoken && chmod 600 /root/.pypitoken
#   ./scripts/upload_pypi.sh [version]
#
# Environment overrides:
#   PYPI_TOKEN_FILE   path of the token file (default /root/.pypitoken)
#   PYPI_USERNAME     default __token__ (that is what API tokens want)
#   KEEP_TOKEN=1      do not shred the token file afterwards

set -euo pipefail

cd "$(dirname "$0")/.."

TOKEN_FILE="${PYPI_TOKEN_FILE:-/root/.pypitoken}"
USERNAME="${PYPI_USERNAME:-__token__}"
VERSION="${1:-}"

if [[ ! -s "$TOKEN_FILE" ]]; then
  echo "no token file at $TOKEN_FILE" >&2
  echo "  echo -n 'pypi-xxxxxxxx' > $TOKEN_FILE && chmod 600 $TOKEN_FILE" >&2
  exit 2
fi

if [[ ! -d dist ]]; then
  echo "dist/ not found — run: python3 -m build" >&2
  exit 2
fi

if [[ -n "$VERSION" ]]; then
  FILES=(dist/foreman_plane-"$VERSION"-py3-none-any.whl dist/foreman_plane-"$VERSION".tar.gz)
else
  FILES=(dist/*)
fi

for f in "${FILES[@]}"; do
  [[ -f "$f" ]] || { echo "missing artefact: $f" >&2; exit 3; }
done

echo "uploading: ${FILES[*]}"

# Skip the upload when the version is already on PyPI — the file name has to
# be unique, so a second upload of the same version is a hard error.
if [[ -n "$VERSION" ]]; then
  if curl -fsS "https://pypi.org/pypi/foreman-plane/$VERSION/json" >/dev/null 2>&1; then
    echo "foreman-plane $VERSION is already on PyPI — nothing to do"
    [[ "${KEEP_TOKEN:-0}" == "1" ]] || shred -u "$TOKEN_FILE" 2>/dev/null || rm -f "$TOKEN_FILE"
    exit 0
  fi
fi

set +e
TWINE_USERNAME="$USERNAME" TWINE_PASSWORD="$(cat "$TOKEN_FILE")" \
  python3 -m twine upload --non-interactive "${FILES[@]}"
rc=$?
set -e

if [[ $rc -eq 0 ]]; then
  echo "upload ok — shredding $TOKEN_FILE"
  [[ "${KEEP_TOKEN:-0}" == "1" ]] || shred -u "$TOKEN_FILE" 2>/dev/null || rm -f "$TOKEN_FILE"
else
  echo "upload FAILED (rc=$rc) — token file left in place: $TOKEN_FILE" >&2
fi
exit $rc
