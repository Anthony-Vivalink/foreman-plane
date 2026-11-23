#!/usr/bin/env bash
# scripts/artifact_gate_drill.sh — the artefact gate, end to end.
#
# The scenario (the one that breaks a plane without attempt semantics):
#
#   1. task A produces its artefact — and is interrupted *before* the
#      plane can write the `done` event
#   2. the plane is restarted: task B, which depends on A, must not run
#      and must not write anything
#   3. the attempt of A is checked (interrupted, lease over) and A is
#      explicitly retried — a *new* attempt with a *new* artefact
#      directory
#   4. only the artefact that passed the acceptance **of this attempt**
#      lets B go
#
# Nothing here is mocked: the worker really writes files, the dispatcher
# is really killed with `kill -9`, the gate really refuses B.
#
# Usage:   scripts/artifact_gate_drill.sh
# Needs:   bash, python3 (>= 3.10).  No network, no real agent, no write
#          outside a throw-away /tmp directory.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
PYTHON="${PYTHON:-python3}"

TASK_A="T-20261123-51"
TASK_B="T-20261123-52"

command -v "${PYTHON}" >/dev/null 2>&1 || {
  echo "drill: ${PYTHON} not found" >&2
  exit 1
}

export PYTHONPATH="${REPO_ROOT}/src${PYTHONPATH:+:${PYTHONPATH}}"

# --- a throw-away plane home, strictly inside /tmp ---------------------
WORK="$(mktemp -d /tmp/foreman-artifact-gate.XXXXXX)"
case "${WORK}" in
  /tmp/*) ;;
  *)
    echo "drill: refusing to work outside /tmp (${WORK})" >&2
    exit 1
    ;;
esac
cleanup() {
  if [ "${DRILL_KEEP:-0}" = "1" ]; then
    echo "drill: the workdir is kept: ${WORK}"
    return
  fi
  rm -rf "${WORK}"
}
trap cleanup EXIT

export FOREMAN_HOME="${WORK}/home"
mkdir -p "${FOREMAN_HOME}"

say() {
  printf '\n== %s ==\n' "$1"
}

run_cli() {
  "${PYTHON}" -m foreman_plane.cli "$@"
}

# --- the worker: it writes into the attempt directory and then holds ---
cat > "${WORK}/worker.py" <<'PY'
"""The worker of the drill: it writes the artefact of *this* attempt."""
import os
import pathlib
import sys
import time

workdir = pathlib.Path(os.environ.get("FOREMAN_WORKDIR") or ".")
attempt_dir = pathlib.Path(os.environ.get("FOREMAN_ATTEMPT_DIR") or ".")
attempt_dir.mkdir(parents=True, exist_ok=True)

name_file = workdir / "artifact_name.txt"
content_file = workdir / "content.txt"
name = name_file.read_text(encoding="utf-8").strip() if name_file.is_file() else "report.json"
content = content_file.read_text(encoding="utf-8").strip() if content_file.is_file() else "EMPTY"

target = attempt_dir / name
target.write_text(content + "\n", encoding="utf-8")
print("worker wrote", target, flush=True)

if (workdir / "hold").exists():
    # the drill kills the dispatcher while the worker holds this attempt
    time.sleep(float(os.environ.get("DRILL_HOLD_S", "120")))
print("worker done", flush=True)
PY

"${PYTHON}" - "${PYTHON}" "${WORK}/worker.py" "${FOREMAN_HOME}/agents.json" <<'PY'
import json
import sys

python, worker, agents_file = sys.argv[1:4]
agents = {
    "agents": {
        "gateagent": {
            "command": '"{}" "{}" {{task}} {{attempt_dir}}'.format(python, worker)
        }
    }
}
with open(agents_file, "w", encoding="utf-8") as handle:
    json.dump(agents, handle, ensure_ascii=False, indent=2)
PY

# --- the two cards -----------------------------------------------------
cat > "${WORK}/card_a.md" <<MD
【id】${TASK_A}
【acceptance】report.json::contains=COMPLETE
Task A produces the report.
MD

cat > "${WORK}/card_b.md" <<MD
【id】${TASK_B}
【depends_on】${TASK_A}
【acceptance】report_b.json::contains=COMPLETE
Task B may only run when A is done and accepted.
MD

# --- a decoy in the base directory -------------------------------------
# It would pass the criterion as well — only the recorded path and the
# recorded sha256 prove which file was really accepted.
printf 'COMPLETE but STALE\n' > "${WORK}/report.json"

show_state() {
  run_cli status --task-id "$1" --json | "${PYTHON}" -c '
import json, sys
state = (json.load(sys.stdin) or {}).get("task") or {}
for key in ("status", "attempt_id", "epoch", "attempt_state", "late_receipts", "lease_expires_at"):
    print("  {:<18}: {}".format(key, state.get(key)))
'
}

say "1. a fresh plane, two cards, one worker"
echo "FOREMAN_HOME : ${FOREMAN_HOME}"
echo "task A       : ${TASK_A}  (produces report.json)"
echo "task B       : ${TASK_B}  (depends on A, produces report_b.json)"
echo "decoy        : ${WORK}/report.json  (COMPLETE but STALE — it would pass the criterion)"

# ======================================================================
say "2. dispatch A — the worker writes the artefact and is then killed"
printf 'report.json\n' > "${WORK}/artifact_name.txt"
printf 'PARTIAL\n' > "${WORK}/content.txt"
: > "${WORK}/hold"

set -m
run_cli dispatch --file "${WORK}/card_a.md" --agent gateagent --workdir "${WORK}" --json \
  > "${WORK}/dispatch_a1.json" 2>&1 &
DISPATCH_PID=$!
set +m
disown "${DISPATCH_PID}" 2>/dev/null || true
echo "dispatcher pid: ${DISPATCH_PID}"

ARTIFACT=""
for _ in $(seq 1 200); do
  ARTIFACT="$(find "${WORK}/attempts" -name 'report.json' -type f 2>/dev/null | head -1 || true)"
  if [ -n "${ARTIFACT}" ]; then
    break
  fi
  sleep 0.1
done
if [ -z "${ARTIFACT}" ]; then
  echo "drill: the worker never produced its artefact" >&2
  exit 1
fi
echo "the artefact of the first attempt: ${ARTIFACT}"
echo "its content: $(cat "${ARTIFACT}")"

say "3. kill -9 the dispatcher before it can write the done event"
CHILDREN="$(ps -o pid= --ppid "${DISPATCH_PID}" 2>/dev/null | tr -d ' ' || true)"
PGID="$(ps -o pgid= -p "${DISPATCH_PID}" 2>/dev/null | tr -d ' ' || true)"
if [ -n "${PGID}" ] && [ "${PGID}" = "${DISPATCH_PID}" ]; then
  kill -9 -- "-${DISPATCH_PID}" 2>/dev/null || true
else
  for pid in ${CHILDREN}; do
    kill -9 "${pid}" 2>/dev/null || true
  done
  kill -9 "${DISPATCH_PID}" 2>/dev/null || true
fi
wait "${DISPATCH_PID}" 2>/dev/null || true
rm -f "${WORK}/hold"
echo "dispatcher killed — the ledger has no done event for ${TASK_A}"

say "4. the ledger after the crash"
run_cli ledger show "${TASK_A}"
show_state "${TASK_A}"

say "5. the plane is restarted — task B must not run"
set +e
BLOCKED="$(run_cli dispatch --file "${WORK}/card_b.md" --agent gateagent --workdir "${WORK}" --json 2>&1)"
BLOCKED_RC=$?
set -e
echo "${BLOCKED}"
echo "exit code: ${BLOCKED_RC} (2 = the gate refused the task)"
if [ "${BLOCKED_RC}" -ne 2 ]; then
  echo "drill: B should have been blocked" >&2
  exit 1
fi
if [ -e "${WORK}/report_b.json" ] || find "${WORK}/attempts" -name 'report_b.json' -type f 2>/dev/null | grep -q .; then
  echo "drill: B wrote something although it was blocked" >&2
  exit 1
fi
echo "B wrote nothing — the gate held"

say "6. check the attempt of A: the lease is alive, the worker is presumed dead"
show_state "${TASK_A}"
echo "the attempt of A is 'still_running' — the lease is still valid"

say "7. let the lease expire, then reclaim A"
NOW_FUTURE="$("${PYTHON}" -c 'import datetime; print((datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(hours=1)).isoformat())')"
echo "evaluating the leases at ${NOW_FUTURE} (one hour ahead)"
run_cli reclaim --now "${NOW_FUTURE}" --json
show_state "${TASK_A}"

say "8. explicitly retry A — a new attempt, a new artefact directory"
printf 'COMPLETE\n' > "${WORK}/content.txt"
run_cli retry "${TASK_A}" --card "${WORK}/card_a.md" --agent gateagent --workdir "${WORK}" --json \
  > "${WORK}/dispatch_a2.json" 2>&1
cat "${WORK}/dispatch_a2.json"

say "9. what the acceptance really accepted"
"${PYTHON}" - "${WORK}" "${TASK_A}" <<'PY'
import hashlib
import json
import pathlib
import sys

work = pathlib.Path(sys.argv[1])
task_id = sys.argv[2]
ledger = work / "home" / "ledger.jsonl"
dones = []
for line in ledger.read_text(encoding="utf-8").splitlines():
    if not line.strip():
        continue
    try:
        record = json.loads(line)
    except ValueError:
        continue
    if record.get("event") == "done" and record.get("task_id") == task_id:
        dones.append(record)
for record in dones:
    print("  accepted path  :", record.get("acceptance_path"))
    print("  accepted sha256:", record.get("acceptance_sha256"))
    print("  attempt        :", record.get("attempt_id"), "epoch", record.get("epoch"))
print("  done events    :", len(dones))
PY

say "10. the artefacts on disk"
find "${WORK}/attempts" -name '*.json' -type f | sort | while read -r file; do
  echo "  ${file#"${WORK}"/} : $(cat "${file}")"
done
echo "  the decoy ${WORK}/report.json : $(cat "${WORK}/report.json")"

say "11. now B may run"
printf 'report_b.json\n' > "${WORK}/artifact_name.txt"
printf 'COMPLETE\n' > "${WORK}/content.txt"
run_cli dispatch --file "${WORK}/card_b.md" --agent gateagent --workdir "${WORK}" --json \
  > "${WORK}/dispatch_b.json" 2>&1
cat "${WORK}/dispatch_b.json"

say "summary"
"${PYTHON}" - "${WORK}" "${TASK_A}" "${TASK_B}" <<'PY'
import hashlib
import json
import pathlib
import sys

work = pathlib.Path(sys.argv[1])
task_a, task_b = sys.argv[2], sys.argv[3]
records = []
for line in (work / "home" / "ledger.jsonl").read_text(encoding="utf-8").splitlines():
    if not line.strip():
        continue
    try:
        records.append(json.loads(line))
    except ValueError:
        pass

dones = [r for r in records if r.get("event") == "done" and r.get("task_id") == task_a]
accepted = dones[-1] if dones else {}
accepted_path = pathlib.Path(accepted.get("acceptance_path") or "")
accepted_sha = accepted.get("acceptance_sha256")

attempt_dirs = sorted(
    path for path in (work / "attempts").glob("*") if path.name.startswith(task_a)
)
first = attempt_dirs[0] if attempt_dirs else None
second = attempt_dirs[1] if len(attempt_dirs) > 1 else None

def sha(path):
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return None

print("attempts of A      :", ", ".join(p.name for p in attempt_dirs))
print("accepted artefact  :", accepted_path.name if accepted_path.name else "-")
print("accepted in attempt:", accepted.get("attempt_id"))
print("sha256 accepted    :", accepted_sha)
print("sha256 of attempt 1:", sha(first / "report.json") if first else "-")
print("sha256 of attempt 2:", sha(second / "report.json") if second else "-")
print("sha256 of the decoy:", sha(work / "report.json"))

b_files = sorted(
    path / "report_b.json"
    for path in (work / "attempts").glob("*")
    if (path / "report_b.json").is_file()
)
print("B wrote            :", b_files[0].name if b_files else "nothing")

problems = []
if len(dones) != 1:
    problems.append(f"A has {len(dones)} done events, expected 1")
if not accepted_path:
    problems.append("nothing was accepted")
elif second and accepted_path.parent != second:
    problems.append(f"the accepted artefact is not the one of {second.name}")
if accepted_sha and second:
    if accepted_sha != sha(second / "report.json"):
        problems.append("the accepted sha256 does not match the artefact of the last attempt")
    if first and accepted_sha == sha(first / "report.json"):
        problems.append("the accepted sha256 is the one of the first attempt")
if not b_files:
    problems.append("B never ran")

for item in problems:
    print("PROBLEM:", item)
print("drill result:", "OK" if not problems else "BROKEN")
sys.exit(1 if problems else 0)
PY

printf '\ndrill OK — the interrupted attempt was not accepted, the retried attempt was, and only then B ran.\n'
