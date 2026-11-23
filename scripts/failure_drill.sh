#!/usr/bin/env bash
# scripts/failure_drill.sh — the failure & recovery story, end to end.
#
# Nothing here is mocked.  The drill proves the whole chain of v0.3.0:
#
#   1. a worker is killed with `kill -9` in the middle of a three step task
#   2. the ledger stays on "running" and a re-dispatch is refused
#   3. the lease expires, `reclaim` marks the task "stale"
#   4. the task is dispatched again — the finished step is NOT run twice
#
# Usage:   scripts/failure_drill.sh
# Needs:   bash, python3 (>= 3.10), ps.  No network, no real agent, no home
#          directory writes — everything happens in a throw-away /tmp dir.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
PYTHON="${PYTHON:-python3}"
TASK_ID="T-20261123-01"

command -v "${PYTHON}" >/dev/null 2>&1 || {
  echo "drill: ${PYTHON} not found" >&2
  exit 1
}
command -v ps >/dev/null 2>&1 || {
  echo "drill: ps not found" >&2
  exit 1
}

export PYTHONPATH="${REPO_ROOT}/src${PYTHONPATH:+:${PYTHONPATH}}"

# --- a throw-away plane home, strictly inside /tmp ---------------------
WORK="$(mktemp -d /tmp/foreman-drill.XXXXXX)"
case "${WORK}" in
  /tmp/*) ;;
  *)
    echo "drill: refusing to work outside /tmp (${WORK})" >&2
    exit 1
    ;;
esac
cleanup() {
  rm -rf "${WORK}"
}
trap cleanup EXIT

export FOREMAN_HOME="${WORK}/home"
export DRILL_AGENT_LOG="${WORK}/agent.log"
mkdir -p "${FOREMAN_HOME}"

say() {
  printf '\n== %s ==\n' "$1"
}

# --- the fake worker: three steps, one checkpoint per step -------------
cat > "${WORK}/fake_agent.py" <<'PY'
"""The fake worker of the drill: three steps, one checkpoint per step."""
import json
import os
import re
import subprocess
import sys
import time

task = sys.argv[1] if len(sys.argv) > 1 else ""
raw = sys.argv[2] if len(sys.argv) > 2 else "[]"
try:
    completed = json.loads(raw)
except ValueError:
    completed = []
if not isinstance(completed, list):
    completed = []

log = os.environ.get("DRILL_AGENT_LOG", "")


def note(line):
    print(line)
    sys.stdout.flush()
    if log:
        with open(log, "a", encoding="utf-8") as handle:
            handle.write(line + "\n")


match = re.search(r"T-\d{8}-\d{2,}", task)
task_id = match.group(0) if match else ""
steps = ["step_a", "step_b", "step_c"]
sleep_s = float(os.environ.get("DRILL_STEP_SLEEP", "5"))

for step in steps:
    if step in completed:
        note(f"SKIP {step} (already checkpointed)")
        continue
    note(f"RUN {step}")
    if task_id:
        subprocess.run(
            [sys.executable, "-m", "foreman_plane.cli", "checkpoint", task_id, "--step", step],
            check=False,
            stdout=subprocess.DEVNULL,
        )
    if step != steps[-1]:
        time.sleep(sleep_s)
note("WORKER DONE")
PY

# --- the plane home: one agent, one card --------------------------------
"${PYTHON}" - "${PYTHON}" "${WORK}/fake_agent.py" "${FOREMAN_HOME}/agents.json" <<'PY'
import json
import sys

python, agent_script, agents_file = sys.argv[1:4]
agents = {
    "agents": {
        "fake": {
            "command": '"{}" "{}" {{task}} {{completed_steps}}'.format(python, agent_script)
        }
    }
}
with open(agents_file, "w", encoding="utf-8") as handle:
    json.dump(agents, handle, ensure_ascii=False, indent=2)
PY

cat > "${WORK}/card.md" <<'MD'
【id】T-20261123-01
三步任务：step_a → step_b → step_c。每完成一步写一次 checkpoint。
MD

# --- a small status formatter ------------------------------------------
cat > "${WORK}/show.py" <<'PY'
import json
import sys

payload = json.load(sys.stdin)
state = payload.get("task") or {}
print("  status            : {}".format(state.get("status")))
print("  lease_expires_at  : {}".format(state.get("lease_expires_at")))
print("  lease_remaining_s : {}".format(state.get("lease_remaining_s")))
print("  age_s             : {}".format(state.get("age_s")))
print("  completed_steps   : {}".format(state.get("completed_steps")))
print("  recoveries        : {}".format(state.get("recoveries")))
print("  retries           : {}".format(state.get("retries")))
PY

run_cli() {
  "${PYTHON}" -m foreman_plane.cli "$@"
}

say "1. a fresh plane and a three step card"
echo "FOREMAN_HOME : ${FOREMAN_HOME}"
echo "agent        : fake (fake_agent.py, writes a checkpoint per step)"
cat "${WORK}/card.md"

say "2. dispatch the card — the worker will be killed half way through"
set -m
run_cli dispatch --file "${WORK}/card.md" --agent fake --workdir "${WORK}" --json \
  > "${WORK}/dispatch1.json" 2>&1 &
DISPATCH_PID=$!
set +m
# the job is killed on purpose below — do not let the shell announce it
disown "${DISPATCH_PID}" 2>/dev/null || true
echo "dispatcher pid: ${DISPATCH_PID}"

# wait for the first checkpoint (the worker sleeps between the steps)
CHECKPOINTED=0
for _ in $(seq 1 200); do
  if grep -q '"event": "checkpoint"' "${FOREMAN_HOME}/ledger.jsonl" 2>/dev/null; then
    CHECKPOINTED=1
    break
  fi
  sleep 0.1
done
if [ "${CHECKPOINTED}" -ne 1 ]; then
  echo "drill: the worker never reached its first checkpoint" >&2
  exit 1
fi
echo "first checkpoint seen — the worker is now sleeping in step_b"

say "3. kill -9 the worker (the whole process group of the dispatcher)"
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
echo "worker killed — the ledger keeps the last event the worker wrote"

say "4. the task is stuck in running: the lease is alive, a re-dispatch is refused"
run_cli status --task-id "${TASK_ID}" --json | "${PYTHON}" "${WORK}/show.py"

set +e
REFUSED="$(run_cli dispatch --file "${WORK}/card.md" --agent fake --workdir "${WORK}" --json 2>&1)"
REFUSED_RC=$?
set -e
echo "${REFUSED}"
echo "exit code: ${REFUSED_RC} (2 = refused, the task still holds its lease)"
if [ "${REFUSED_RC}" -ne 2 ]; then
  echo "drill: the re-dispatch should have been refused" >&2
  exit 1
fi

say "5. let the lease expire, then reclaim the task"
NOW_FUTURE="$("${PYTHON}" -c 'import datetime; print((datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(hours=1)).isoformat())')"
echo "evaluating the leases at ${NOW_FUTURE} (one hour ahead)"
run_cli reclaim --now "${NOW_FUTURE}" --json

say "6. dispatch the task again — the finished step must not run twice"
export DRILL_STEP_SLEEP=0.2
run_cli dispatch --file "${WORK}/card.md" --agent fake --workdir "${WORK}" --json \
  > "${WORK}/dispatch2.json" 2>&1
cat "${WORK}/dispatch2.json"

say "7. the whole ledger"
run_cli ledger show "${TASK_ID}"

say "8. what the worker actually did"
cat "${WORK}/agent.log"

say "summary"
COMPLETED="$("${PYTHON}" -c 'import json,sys; print(",".join(json.load(open(sys.argv[1]))["completed_steps"]))' "${WORK}/dispatch2.json")"
RUN_A="$(grep -c '^RUN step_a$' "${WORK}/agent.log" || true)"
RUN_B="$(grep -c '^RUN step_b$' "${WORK}/agent.log" || true)"
RUN_C="$(grep -c '^RUN step_c$' "${WORK}/agent.log" || true)"
STALE="$(grep -c '"event": "stale"' "${FOREMAN_HOME}/ledger.jsonl" || true)"
FINAL="$(run_cli status --task-id "${TASK_ID}" --json | "${PYTHON}" -c 'import json,sys; s=json.load(sys.stdin)["task"]; print(s.get("status"), s.get("verdict"))')"

echo "recoveries (stale events) : ${STALE}"
echo "steps handed back as done : ${COMPLETED}"
echo "step_a executed times     : ${RUN_A}  (1 = no duplicated work)"
echo "step_b executed times     : ${RUN_B}"
echo "step_c executed times     : ${RUN_C}"
echo "final status              : ${FINAL}"

if [ "${RUN_A}" -ne 1 ]; then
  echo "drill: step_a ran ${RUN_A} times — the checkpoint was not honoured" >&2
  exit 1
fi
if [ "${RUN_B}" -ne 1 ] || [ "${RUN_C}" -ne 1 ]; then
  echo "drill: the remaining steps did not run exactly once" >&2
  exit 1
fi
if [ "${STALE}" -lt 1 ]; then
  echo "drill: the task was never reclaimed" >&2
  exit 1
fi
case "${FINAL}" in
  done\ pass) ;;
  *)
    echo "drill: the recovered task did not finish (${FINAL})" >&2
    exit 1
    ;;
esac

printf '\ndrill OK — the worker died, the task was reclaimed, the finished step was not repeated.\n'
