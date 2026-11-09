"""Task card contract (SPEC §2).

Four markers may appear anywhere in the task text:

============  ==================================  =====================
marker        meaning                              field
============  ==================================  =====================
``【id】``     task id ``T-YYYYMMDD-NN``           ``task_id``
``【depends_on】``  hard dependency (must be done+pass)  ``depends_on``
``【needs_api】``   soft dependency (artefact ready)      ``needs_api``
``【验收】``   acceptance artefact (+ criteria)    ``acceptance``
============  ==================================  =====================

Parsing never raises.  A marker that is present but yields no legal token
is reported through an explicit ``*_unparsed`` flag — never as "no
dependency", which would silently disable a hard gate.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date

__all__ = [
    "TaskCard",
    "parse_task_card",
    "is_valid_task_id",
    "split_acceptance",
    "TASK_ID_RE",
    "PATH_TOKEN_RE",
]

#: Task id shape: ``T-`` + 8 digits (a real calendar date) + ``-`` + 2+ digits.
TASK_ID_RE = re.compile(r"T-\d{8}-\d{2,}")

#: Legal path token (SPEC §2.2).  No spaces, no CJK punctuation.
PATH_TOKEN_RE = re.compile(r"^[A-Za-z0-9_\-./~]{1,200}(?:#[A-Za-z0-9_\-./~]{1,200})?$")

#: Acceptance path: same character class, without the ``id#path`` prefix form.
ACCEPT_PATH_RE = re.compile(r"^[A-Za-z0-9_\-./~]{1,200}$")

_MARKER_NAME = r"(?P<name>id|depends_on|needs_api|验收)"

#: ``【id】`` / ``[id]`` / ``【id:】`` / ``[id]:`` — bracketed form.
_BRACKETED_RE = re.compile(
    r"[【\[]\s*" + _MARKER_NAME + r"\s*[:：]?\s*[】\]]\s*[:：]?\s*",
    re.IGNORECASE,
)

#: ``depends_on: ...`` — colon form.  The look-behind keeps JSON-ish bodies
#: (``{"depends_on": []}``) from being mistaken for a marker while still
#: accepting a marker that follows CJK text or whitespace.
_COLON_RE = re.compile(
    r"(?<![A-Za-z0-9_\"'\[{])" + _MARKER_NAME + r"\s*[:：]\s*",
    re.IGNORECASE,
)


@dataclass
class TaskCard:
    """Parsed task card (SPEC §2.3)."""

    raw: str
    task_id: str | None = None
    depends_on: list[str] = field(default_factory=list)
    needs_api: list[str] = field(default_factory=list)
    acceptance: str | None = None
    depends_on_unparsed: bool = False
    needs_api_unparsed: bool = False
    acceptance_unparsed: bool = False


def _valid_ymd(digits: str) -> bool:
    """Real calendar date in 2000-2099 (``20260230`` is not a date)."""
    try:
        year, month, day = int(digits[0:4]), int(digits[4:6]), int(digits[6:8])
    except (TypeError, ValueError):
        return False
    if not 2000 <= year <= 2099:
        return False
    try:
        date(year, month, day)
    except ValueError:
        return False
    return True


def is_valid_task_id(candidate: str) -> bool:
    """True when ``candidate`` is a well formed task id with a real date."""
    if not candidate:
        return False
    match = TASK_ID_RE.fullmatch(candidate)
    if match is None:
        return False
    return _valid_ymd(candidate[2:10])


def _extract_task_ids(text: str) -> list[str]:
    """All *legal* task ids mentioned in ``text`` (annotations are ignored)."""
    found: list[str] = []
    for match in TASK_ID_RE.finditer(text or ""):
        token = match.group(0)
        if is_valid_task_id(token) and token not in found:
            found.append(token)
    return found


def split_acceptance(value: str | None) -> tuple[str | None, list[tuple[str, str]]]:
    """Split ``path::key=value::key=value`` into path and criteria pairs."""
    if value is None:
        return None, []
    raw = str(value).strip()
    if not raw:
        return None, []
    parts = raw.split("::")
    path = parts[0].strip()
    criteria: list[tuple[str, str]] = []
    for part in parts[1:]:
        chunk = part.strip()
        if not chunk:
            continue
        if "=" not in chunk:
            criteria.append((chunk, ""))
            continue
        key, val = chunk.split("=", 1)
        criteria.append((key.strip(), val))
    return (path or None), criteria


def _find_markers(text: str) -> list[tuple[int, int, str]]:
    """Locate every marker occurrence as ``(start, end, name)``."""
    raw: list[tuple[int, int, str]] = []
    for regex in (_BRACKETED_RE, _COLON_RE):
        for match in regex.finditer(text):
            raw.append((match.start(), match.end(), match.group("name").lower()))
    raw.sort(key=lambda item: (item[0], -(item[1] - item[0])))
    markers: list[tuple[int, int, str]] = []
    consumed_to = -1
    for start, end, name in raw:
        if start < consumed_to:
            continue
        markers.append((start, end, name))
        consumed_to = end
    return markers


def _looks_like_value(kind: str, chunk: str) -> bool:
    """Heuristic used when a marker carries its value on the next lines."""
    if kind in ("id", "depends_on"):
        return bool(TASK_ID_RE.search(chunk))
    if kind == "needs_api":
        parts = [p for p in re.split(r"[\s,，]+", chunk) if p]
        return bool(parts) and all(PATH_TOKEN_RE.match(p) for p in parts)
    # 验收: a path token optionally followed by ::criteria
    return bool(re.match(r"^[A-Za-z0-9_\-./~]{1,200}(::|$)", chunk))


def _candidate_values(text: str, markers: list[tuple[int, int, str]], index: int, kind: str) -> list[str]:
    """Raw value candidates belonging to one marker occurrence."""
    start = markers[index][1]
    end = markers[index + 1][0] if index + 1 < len(markers) else len(text)
    lines = (text[start:end]).split("\n")
    inline = lines[0].strip()
    if inline:
        return [inline]
    values: list[str] = []
    for line in lines[1:]:
        chunk = line.strip()
        if not chunk:
            break
        if not _looks_like_value(kind, chunk):
            break
        values.append(chunk)
    return values


def parse_task_card(text: str) -> TaskCard:
    """Parse a task card.  Never raises — see the ``*_unparsed`` flags."""
    raw = "" if text is None else (text if isinstance(text, str) else str(text))
    card = TaskCard(raw=raw)
    try:
        markers = _find_markers(raw)
        by_name: dict[str, list[int]] = {}
        for index, (_s, _e, name) in enumerate(markers):
            by_name.setdefault(name, []).append(index)

        if "id" in by_name:
            ids: list[str] = []
            for index in by_name["id"]:
                for value in _candidate_values(raw, markers, index, "id"):
                    ids.extend(_extract_task_ids(value))
            if ids:
                card.task_id = ids[0]

        if "depends_on" in by_name:
            deps: list[str] = []
            for index in by_name["depends_on"]:
                for value in _candidate_values(raw, markers, index, "depends_on"):
                    for token in _extract_task_ids(value):
                        if token not in deps:
                            deps.append(token)
            card.depends_on = deps
            card.depends_on_unparsed = not deps

        if "needs_api" in by_name:
            tokens: list[str] = []
            for index in by_name["needs_api"]:
                for value in _candidate_values(raw, markers, index, "needs_api"):
                    if PATH_TOKEN_RE.match(value) and value not in tokens:
                        tokens.append(value)
            card.needs_api = tokens
            card.needs_api_unparsed = not tokens

        if "验收" in by_name:
            candidates: list[str] = []
            usable = False
            for index in by_name["验收"]:
                for value in _candidate_values(raw, markers, index, "验收"):
                    candidates.append(value)
                    path, _criteria = split_acceptance(value)
                    if path and ACCEPT_PATH_RE.match(path):
                        usable = True
            card.acceptance = candidates[0] if candidates else None
            card.acceptance_unparsed = not usable
    except Exception:  # pragma: no cover - defensive, parsing must not raise
        card.task_id = None
        card.depends_on = []
        card.needs_api = []
        card.acceptance = None
        card.depends_on_unparsed = "depends_on" in raw.lower()
        card.needs_api_unparsed = "needs_api" in raw.lower()
        card.acceptance_unparsed = "验收" in raw
    return card
