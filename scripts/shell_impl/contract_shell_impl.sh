#!/usr/bin/env bash
# scripts/shell_impl/contract_shell_impl.sh
#
# The **shell side** of the shared contract.
#
# It computes exactly the facts `tests/contract_probe.py` computes — the
# facts of the task card, of the JSONL ledgers and of the acceptance
# sheet — from exactly the same fixture files.  `scripts/contract_fixture_check.sh`
# compares the two, so a local shell plane and the public package can not
# drift apart.
#
# Usage:  contract_shell_impl.sh [FIXTURES_DIR]
# Env:    FOREMAN_FIXTURES_DIR   (default: <package>/tests/fixtures)
# Tools:  awk, sed, grep, sha256sum, wc — no python, no jq, no network.
#
# The fixture directory is a *parameter*: a local plane points it at its
# own copy (or at the copy of the package) with an environment variable.
# No absolute path of any host is baked into this file.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PACKAGE_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
FIXTURES="${1:-${FOREMAN_FIXTURES_DIR:-${PACKAGE_ROOT}/tests/fixtures}}"

if [ ! -d "${FIXTURES}" ]; then
  echo "contract_shell_impl: no fixture directory: ${FIXTURES}" >&2
  exit 1
fi

MANIFEST="${FIXTURES}/manifest.json"
if [ ! -f "${MANIFEST}" ]; then
  echo "contract_shell_impl: no manifest: ${MANIFEST}" >&2
  exit 1
fi

# --- read the manifest (one key per line, controlled format) ------------
manifest_value() {
  sed -n "s/^[[:space:]]*\"$1\":[[:space:]]*\"\([^\"]*\)\".*/\1/p" "${MANIFEST}" | head -1
}

REFERENCE_TIME="$(manifest_value reference_time)"
CARD_FILE="${FIXTURES}/$(manifest_value card)"
LEDGER_ATTEMPT="${FIXTURES}/$(manifest_value ledger_attempt)"
LEDGER_LEGACY="${FIXTURES}/$(manifest_value ledger_legacy)"
LEDGER_CORRUPT="${FIXTURES}/$(manifest_value ledger_corrupt)"
SHEET_FILE="${FIXTURES}/$(manifest_value acceptance)"

if [ -z "${REFERENCE_TIME}" ]; then
  echo "contract_shell_impl: the manifest has no reference_time" >&2
  exit 1
fi

json_str() {
  # a minimal JSON string escaper for the values of the contract
  printf '%s' "$1" | sed 's/\\/\\\\/g; s/"/\\"/g'
}

# ======================================================================
# 1. the task card
# ======================================================================
CARD_LINES="$(sed -n '/^  "lines": \[$/,/^  \],$/p' "${CARD_FILE}" \
  | sed -n 's/^    "\(.*\)",\{0,1\}$/\1/p')"

CARD_TASK_ID=""
CARD_DEPENDS=()
CARD_NEEDS=()
CARD_ACCEPT=""
CARD_ACCEPT_PATH=""
CARD_CRITERIA=()

while IFS= read -r line; do
  [ -n "${line}" ] || continue
  case "${line}" in
    "[id]"*)
      CARD_TASK_ID="$(printf '%s' "${line}" | grep -oE 'T-[0-9]{8}-[0-9]+' | head -1)"
      ;;
    "[depends_on]"*)
      while IFS= read -r token; do
        [ -n "${token}" ] && CARD_DEPENDS+=("${token}")
      done < <(printf '%s' "${line}" | grep -oE 'T-[0-9]{8}-[0-9]+')
      ;;
    "[needs_api]"*)
      while IFS= read -r token; do
        [ -n "${token}" ] && CARD_NEEDS+=("${token}")
      done < <(printf '%s' "${line}" | sed 's/^\[needs_api\][[:space:]]*//' | tr ' ,' '\n\n' | grep -v '^$')
      ;;
    "[acceptance]"*)
      CARD_ACCEPT="$(printf '%s' "${line}" | sed 's/^\[acceptance\][[:space:]]*//')"
      CARD_ACCEPT_PATH="${CARD_ACCEPT%%::*}"
      rest="${CARD_ACCEPT#*::}"
      if [ "${rest}" = "${CARD_ACCEPT}" ]; then
        rest=""
      fi
      while IFS= read -r token; do
        [ -n "${token}" ] && CARD_CRITERIA+=("${token}")
      done < <(printf '%s' "${rest}" | tr ':' '\n' | grep -v '^$')
      ;;
  esac
done <<< "${CARD_LINES}"

json_array() {
  local first=1 item
  printf '['
  for item in "$@"; do
    if [ "${first}" -eq 1 ]; then first=0; else printf ', '; fi
    printf '"%s"' "$(json_str "${item}")"
  done
  printf ']'
}

CARD_DEPENDS_JSON="$(json_array "${CARD_DEPENDS[@]+"${CARD_DEPENDS[@]}"}")"
CARD_NEEDS_JSON="$(json_array "${CARD_NEEDS[@]+"${CARD_NEEDS[@]}"}")"
CARD_CRITERIA_JSON="$(json_array "${CARD_CRITERIA[@]+"${CARD_CRITERIA[@]}"}")"

if [ "${#CARD_DEPENDS[@]}" -eq 0 ] && printf '%s' "${CARD_LINES}" | grep -q '^\[depends_on\]'; then
  CARD_DEPENDS_UNPARSED="true"
else
  CARD_DEPENDS_UNPARSED="false"
fi
if [ "${#CARD_NEEDS[@]}" -eq 0 ] && printf '%s' "${CARD_LINES}" | grep -q '^\[needs_api\]'; then
  CARD_NEEDS_UNPARSED="true"
else
  CARD_NEEDS_UNPARSED="false"
fi
if [ -z "${CARD_ACCEPT_PATH}" ] && printf '%s' "${CARD_LINES}" | grep -q '^\[acceptance\]'; then
  CARD_ACCEPTANCE_UNPARSED="true"
else
  CARD_ACCEPTANCE_UNPARSED="false"
fi

# ======================================================================
# 2. the ledgers
# ======================================================================
ledger_facts() {
  # $1 = the ledger file, $2 = the reference time
  awk -v REF="$2" '
    function jstr(line, key,   re, s) {
      re = "\"" key "\": *\"[^\"]*\""
      if (match(line, re)) {
        s = substr(line, RSTART, RLENGTH)
        sub(/^"[^"]*": *"/, "", s)
        sub(/"$/, "", s)
        return s
      }
      return ""
    }
    function jnum(line, key,   re, s) {
      re = "\"" key "\": *-?[0-9]+"
      if (match(line, re)) {
        s = substr(line, RSTART, RLENGTH)
        sub(/^"[^"]*": */, "", s)
        return s
      }
      return ""
    }
    function superseded(tid, ep) {
      if (epoch_of[tid] == "") return 0        # legacy ledger: 0.2.x rule
      if (ep != "" && ep != epoch_of[tid]) return 1
      if (status_of[tid] == "cancelled") return 1
      return 0
    }
    function attempt_state(tid,   st, lt) {
      st = status_of[tid]
      lt = (tid in late) ? late[tid] : 0
      if (st == "done") return "finished"
      if (st == "cancelled") return (lt > 0) ? "outcome_unknown" : "finished"
      if (st == "blocked") return "not_started"
      if (st == "stale") return "interrupted"
      if (st == "running" || st == "dispatched") {
        if (lease_of[tid] == "") return "outcome_unknown"
        return (lease_of[tid] > REF) ? "still_running" : "interrupted"
      }
      return "outcome_unknown"
    }
    {
      line = $0
      gsub(/^[ \t]+|[ \t]+$/, "", line)
      if (line == "") next
      if (line !~ /^\{/ || line !~ /\}$/) { ignored++; next }
      usable++
      ev = jstr(line, "event")
      tid = jstr(line, "task_id")
      if (tid == "") next
      if (!(tid in seen)) {
        seen[tid] = 1
        order[++ntasks] = tid
        status_of[tid] = "unknown"
      }
      ep = jnum(line, "epoch")
      aid = jstr(line, "attempt_id")
      lease = jstr(line, "lease_expires_at")
      if (lease != "") lease_of[tid] = lease
      if (ev == "attempt" || ev == "dispatch") {
        if (ep != "") epoch_of[tid] = ep
        if (aid != "") att[tid] = aid
        if (ev == "dispatch") status_of[tid] = "dispatched"
      } else if (ev == "running") {
        if (superseded(tid, ep)) late[tid]++
        else status_of[tid] = "running"
      } else if (ev == "stale") {
        status_of[tid] = "stale"
      } else if (ev == "blocked") {
        status_of[tid] = "blocked"
      } else if (ev == "cancelled") {
        status_of[tid] = "cancelled"
      } else if (ev == "done") {
        if (superseded(tid, ep)) late[tid]++
        else status_of[tid] = "done"
      } else if (ev == "checkpoint") {
        step = jstr(line, "step")
        if (step != "") {
          key = tid SUBSEP step
          if (!(key in stepseen)) {
            stepseen[key] = 1
            steps[tid] = (steps[tid] == "") ? step : steps[tid] "," step
          }
        }
      }
      # an unknown event kind is ignored on purpose
    }
    END {
      printf "{\"usable_lines\": %d, \"ignored_lines\": %d, \"tasks\": {", usable, ignored
      for (i = 1; i <= ntasks; i++) {
        tid = order[i]
        if (i > 1) printf ", "
        printf "\"%s\": {\"status\": \"%s\", ", tid, status_of[tid]
        if (tid in att && att[tid] != "") printf "\"attempt_id\": \"%s\", ", att[tid]
        else printf "\"attempt_id\": null, "
        if (tid in epoch_of && epoch_of[tid] != "") printf "\"epoch\": %s, ", epoch_of[tid]
        else printf "\"epoch\": null, "
        printf "\"completed_steps\": ["
        if (tid in steps && steps[tid] != "") {
          n = split(steps[tid], arr, ",")
          for (j = 1; j <= n; j++) {
            if (j > 1) printf ", "
            printf "\"%s\"", arr[j]
          }
        }
        printf "], \"attempt_state\": \"%s\", \"late_receipts\": %d}", attempt_state(tid), (tid in late) ? late[tid] : 0
      }
      printf "}}"
    }
  ' "$1"
}

# ======================================================================
# 3. the acceptance sheet
# ======================================================================
acceptance_facts() {
  local name acceptance path criteria key value ok reason sha size file
  local first=1
  printf '['
  while IFS= read -r name && IFS= read -r acceptance; do
    [ -n "${name}" ] || continue
    path="${acceptance%%::*}"
    if [ "${path}" = "${acceptance}" ]; then
      criteria=""
    else
      criteria="${acceptance#*::}"
    fi
    file="${FIXTURES}/${path}"
    ok="true"
    reason="null"
    sha="null"
    size="null"
    if [ ! -f "${file}" ]; then
      ok="false"
      reason='"artifact_missing"'
    else
      sha="\"$(sha256sum "${file}" | cut -d' ' -f1)\""
      size="$(wc -c < "${file}" | tr -d ' ')"
      while IFS= read -r criterion; do
        [ -n "${criterion}" ] || continue
        key="${criterion%%=*}"
        value="${criterion#*=}"
        case "${key}" in
          min_bytes)
            if [ "${size}" -lt "${value}" ]; then
              ok="false"
              [ "${reason}" = "null" ] && reason='"acceptance_criterion_failed"'
            fi
            ;;
          sha256)
            if [ "${sha}" != "\"${value}\"" ]; then
              ok="false"
              [ "${reason}" = "null" ] && reason='"acceptance_criterion_failed"'
            fi
            ;;
          contains)
            if ! grep -qF -- "${value}" "${file}"; then
              ok="false"
              [ "${reason}" = "null" ] && reason='"acceptance_criterion_failed"'
            fi
            ;;
          *)
            ok="false"
            [ "${reason}" = "null" ] && reason='"acceptance_unknown_criterion"'
            ;;
        esac
      done < <(printf '%s' "${criteria}" | tr ':' '\n' | grep -v '^$')
    fi
    if [ "${first}" -eq 1 ]; then first=0; else printf ', '; fi
    printf '{"name": "%s", "ok": %s, "reason": %s, "sha256": %s, "size_bytes": %s}' \
      "$(json_str "${name}")" "${ok}" "${reason}" "${sha}" "${size}"
  done < <(
    sed -n '/"cases": \[$/,/^  \]$/p' "${SHEET_FILE}" \
      | sed -n 's/^      "\(name\|acceptance\)": "\(.*\)",\{0,1\}$/\2/p'
  )
  printf ']'
}

# ======================================================================
# the document
# ======================================================================
ACCEPTANCE_JSON="$(acceptance_facts)"
CARD_JSON="$(printf '{"task_id": %s, "depends_on": %s, "needs_api": %s, "acceptance_path": %s, "acceptance_criteria": %s, "depends_on_unparsed": %s, "needs_api_unparsed": %s, "acceptance_unparsed": %s}' \
  "$(if [ -n "${CARD_TASK_ID}" ]; then printf '"%s"' "$(json_str "${CARD_TASK_ID}")"; else printf 'null'; fi)" \
  "${CARD_DEPENDS_JSON}" \
  "${CARD_NEEDS_JSON}" \
  "$(if [ -n "${CARD_ACCEPT_PATH}" ]; then printf '"%s"' "$(json_str "${CARD_ACCEPT_PATH}")"; else printf 'null'; fi)" \
  "${CARD_CRITERIA_JSON}" \
  "${CARD_DEPENDS_UNPARSED}" "${CARD_NEEDS_UNPARSED}" "${CARD_ACCEPTANCE_UNPARSED}")"

printf '{"card": %s, "ledgers": {"ledger_attempt": %s, "ledger_legacy": %s, "ledger_corrupt": %s}, "acceptance": %s}\n' \
  "${CARD_JSON}" \
  "$(ledger_facts "${LEDGER_ATTEMPT}" "${REFERENCE_TIME}")" \
  "$(ledger_facts "${LEDGER_LEGACY}" "${REFERENCE_TIME}")" \
  "$(ledger_facts "${LEDGER_CORRUPT}" "${REFERENCE_TIME}")" \
  "${ACCEPTANCE_JSON}"
