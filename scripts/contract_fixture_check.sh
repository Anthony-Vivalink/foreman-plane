#!/usr/bin/env bash
# scripts/contract_fixture_check.sh — the shared contract, as a test.
#
# The contract of foreman-plane is not a document any more: it is a set of
# desensitised fixtures plus the facts both implementations must compute
# from them.
#
#   * the **public implementation** — tests/contract_probe.py drives
#     contract.py / ledger.py / acceptance.py;
#   * the **shell implementation** — scripts/shell_impl/contract_shell_impl.sh
#     computes the same facts with awk/sed/sha256sum only.
#
# A local plane (a shell implementation of its own) plugs in through the
# environment variable FOREMAN_SHELL_IMPL and runs the very same fixture
# directory — no absolute path of any host is baked into this package.
#
# Usage:
#   scripts/contract_fixture_check.sh [--mode verify|cross|expect|shell]
#                                     [--fixtures DIR] [--shell-impl PATH]
#
# Env:
#   FOREMAN_FIXTURES_DIR   fixture directory (default: <package>/tests/fixtures)
#   FOREMAN_SHELL_IMPL     shell implementation (default: the packaged one)
#   FOREMAN_PYTHON         python interpreter (default: python3)
#
# Exit codes: 0 = the contract holds, 1 = it does not, 2 = the environment
# is missing something.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PACKAGE_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

FIXTURES="${FOREMAN_FIXTURES_DIR:-${PACKAGE_ROOT}/tests/fixtures}"
SHELL_IMPL="${FOREMAN_SHELL_IMPL:-${PACKAGE_ROOT}/scripts/shell_impl/contract_shell_impl.sh}"
PYTHON="${FOREMAN_PYTHON:-python3}"
MODE="cross"

usage() {
  sed -n '2,28p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
}

while [ "$#" -gt 0 ]; do
  case "$1" in
    --mode)
      MODE="$2"
      shift 2
      ;;
    --fixtures)
      FIXTURES="$2"
      shift 2
      ;;
    --shell-impl)
      SHELL_IMPL="$2"
      shift 2
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "contract_fixture_check: unknown argument: $1" >&2
      exit 2
      ;;
  esac
done

case "${MODE}" in
  verify|cross|expect|shell) ;;
  *)
    echo "contract_fixture_check: unknown mode: ${MODE}" >&2
    exit 2
    ;;
esac

command -v "${PYTHON}" >/dev/null 2>&1 || {
  echo "contract_fixture_check: ${PYTHON} not found" >&2
  exit 2
}
if [ ! -d "${FIXTURES}" ]; then
  echo "contract_fixture_check: no fixture directory: ${FIXTURES}" >&2
  exit 2
fi
CONTRACT="${FIXTURES}/contract.json"
if [ ! -f "${CONTRACT}" ]; then
  echo "contract_fixture_check: no contract file: ${CONTRACT}" >&2
  exit 2
fi

if [ "${MODE}" = "expect" ]; then
  # the expectations, for a foreign implementation to compare against
  cat "${CONTRACT}"
  exit 0
fi

WORK="$(mktemp -d /tmp/foreman-contract.XXXXXX)"
case "${WORK}" in
  /tmp/*) ;;
  *)
    echo "contract_fixture_check: refusing to work outside /tmp (${WORK})" >&2
    exit 2
    ;;
esac
trap 'rm -rf "${WORK}"' EXIT

PROBE_OUT="${WORK}/probe.json"
SHELL_OUT="${WORK}/shell.json"

echo "fixtures    : ${FIXTURES}"
echo "contract    : ${CONTRACT}"
echo "mode        : ${MODE}"

"${PYTHON}" "${PACKAGE_ROOT}/tests/contract_probe.py" --fixtures "${FIXTURES}" --json \
  > "${PROBE_OUT}"
echo "public impl : tests/contract_probe.py -> ok"

if [ "${MODE}" = "shell" ]; then
  if [ ! -x "${SHELL_IMPL}" ] && [ ! -f "${SHELL_IMPL}" ]; then
    echo "contract_fixture_check: no shell implementation: ${SHELL_IMPL}" >&2
    exit 2
  fi
  bash "${SHELL_IMPL}" "${FIXTURES}" > "${SHELL_OUT}"
  echo "shell impl  : ${SHELL_IMPL} -> ok"
  exec "${PYTHON}" - "${CONTRACT}" "${SHELL_OUT}" <<'PY'
import json
import sys

contract = json.load(open(sys.argv[1], encoding="utf-8"))
shell = json.load(open(sys.argv[2], encoding="utf-8"))
expected = contract.get("expectations") or contract
problems = []
if shell != expected:
    for key in sorted(set(expected) | set(shell)):
        if expected.get(key) != shell.get(key):
            problems.append(key)
print("shell implementation matches the contract" if not problems else "MISMATCH: " + ", ".join(problems))
sys.exit(0 if not problems else 1)
PY
fi

if [ "${MODE}" = "cross" ]; then
  if [ ! -f "${SHELL_IMPL}" ]; then
    echo "contract_fixture_check: no shell implementation: ${SHELL_IMPL}" >&2
    exit 2
  fi
  bash "${SHELL_IMPL}" "${FIXTURES}" > "${SHELL_OUT}"
  echo "shell impl  : ${SHELL_IMPL} -> ok"
fi

"${PYTHON}" - "${CONTRACT}" "${PROBE_OUT}" "${SHELL_OUT}" "${MODE}" <<'PY'
import json
import sys

contract = json.load(open(sys.argv[1], encoding="utf-8"))
probe = json.load(open(sys.argv[2], encoding="utf-8"))
mode = sys.argv[4]
expected = contract.get("expectations") or contract


def comparable(facts):
    """The facts both implementations can compute.

    ``parsed_events`` is the package's own reader count — the shell side
    counts the usable lines instead, so it is not part of the contract.
    """
    out = json.loads(json.dumps(facts))
    for data in (out.get("ledgers") or {}).values():
        data.pop("parsed_events", None)
    return out


def diff(left, right, path=""):
    problems = []
    if isinstance(left, dict) and isinstance(right, dict):
        for key in sorted(set(left) | set(right)):
            problems += diff(left.get(key), right.get(key), f"{path}.{key}")
    elif isinstance(left, list) and isinstance(right, list):
        if len(left) != len(right):
            problems.append(f"{path}: {len(left)} != {len(right)} entries")
        else:
            for index, (one, other) in enumerate(zip(left, right)):
                problems += diff(one, other, f"{path}[{index}]")
    elif left != right:
        problems.append(f"{path}: expected {left!r}, got {right!r}")
    return problems


failed = False

probe_cmp = comparable(probe)
problems = diff(expected, probe_cmp)
if problems:
    failed = True
    print("FAIL public implementation vs contract:")
    for item in problems[:20]:
        print("  -", item)
else:
    print("ok   public implementation matches the contract")

if mode == "cross":
    try:
        shell = json.load(open(sys.argv[3], encoding="utf-8"))
    except (OSError, ValueError) as exc:
        print(f"FAIL shell implementation: {exc}")
        sys.exit(1)
    shell_cmp = comparable(shell)
    problems = diff(expected, shell_cmp)
    if problems:
        failed = True
        print("FAIL shell implementation vs contract:")
        for item in problems[:20]:
            print("  -", item)
    else:
        print("ok   shell implementation matches the contract")
    problems = diff(probe_cmp, shell_cmp)
    if problems:
        failed = True
        print("FAIL public implementation vs shell implementation:")
        for item in problems[:20]:
            print("  -", item)
    else:
        print("ok   both implementations agree with each other")

print("contract OK" if not failed else "contract BROKEN")
sys.exit(1 if failed else 0)
PY
