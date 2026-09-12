"""Pull an agent's intent and its expected deliverable out of its brief.

Deterministic and offline by design: the same brief must always produce the
same answer, at no cost, with nothing leaving the machine.
"""

import re
from typing import List, Optional, Tuple

from orchestra.model import Extraction

_HEADING = re.compile(r"^(#{1,6})\s+(.*?)\s*#*\s*$")

_OUTPUT_HEADINGS = (
    "expected output", "expected outputs", "deliverable", "deliverables",
    "output", "outputs", "success criteria", "return", "returns",
    "definition of done", "acceptance", "acceptance criteria",
    "report contract", "what to return", "return format", "output format",
    "expected result", "expected results",
)

_OBJECTIVE_HEADINGS = (
    "objective", "objectives", "goal", "goals", "task", "your task",
    "the task", "mission", "purpose",
)

_OUTPUT_IMPERATIVES = re.compile(
    r"^(return|report|produce|output|deliver|write up|respond with)\b", re.IGNORECASE)

_OBJECTIVE_IMPERATIVES = re.compile(
    r"^(you are|your job is|your task is|you will)\b", re.IGNORECASE)

_MAX_LINES = 12


def _normalize_heading(text: str) -> str:
    return re.sub(r"[^a-z0-9 ]", "", text.lower()).strip()


def _find_section(brief: str, wanted: Tuple[str, ...]) -> Optional[Tuple[str, str]]:
    """Return (section_body, raw_heading) for the first matching heading."""
    lines = brief.splitlines()
    for i, line in enumerate(lines):
        m = _HEADING.match(line)
        if not m:
            continue
        if _normalize_heading(m.group(2)) not in wanted:
            continue
        level = len(m.group(1))
        body: List[str] = []
        for later in lines[i + 1:]:
            lm = _HEADING.match(later)
            if lm and len(lm.group(1)) <= level:
                break
            body.append(later)
        text = "\n".join(body).strip()
        if text:
            return text, m.group(2).strip()
    return None


def _find_imperative(brief: str, pattern: "re.Pattern") -> Optional[str]:
    """Collect the first matching line plus any immediately following non-blank lines."""
    lines = brief.splitlines()
    for i, line in enumerate(lines):
        if pattern.match(line.strip()):
            block = [line.strip()]
            for later in lines[i + 1:]:
                if not later.strip():
                    break
                block.append(later.strip())
                if len(block) >= 3:
                    break
            return "\n".join(block)
    return None


def _first_paragraph(brief: str) -> str:
    for chunk in brief.split("\n\n"):
        cleaned = chunk.strip()
        if cleaned and not _HEADING.match(cleaned.splitlines()[0]):
            return cleaned
    return ""


def _truncate(text: str) -> str:
    lines = [l for l in text.splitlines()]
    if len(lines) > _MAX_LINES:
        lines = lines[:_MAX_LINES] + ["..."]
    return "\n".join(lines).strip()


def _fallback(brief: str, description: str) -> Extraction:
    parts = [p for p in (description.strip(), _first_paragraph(brief)) if p]
    if not parts:
        return Extraction("", "none")
    return Extraction(_truncate("\n\n".join(parts)), "fallback")


def extract_expected_output(brief: str, description: str = "") -> Extraction:
    brief = brief or ""
    found = _find_section(brief, _OUTPUT_HEADINGS)
    if found:
        return Extraction(_truncate(found[0]), 'heading "{}"'.format(found[1]))
    imperative = _find_imperative(brief, _OUTPUT_IMPERATIVES)
    if imperative:
        return Extraction(_truncate(imperative), "imperative line")
    fallback = _fallback(brief, description)
    if fallback.source == "fallback":
        objective = extract_objective(brief, description)
        if fallback.text.strip() == objective.text.strip():
            return Extraction("", "not stated")
    return fallback


def extract_objective(brief: str, description: str = "") -> Extraction:
    brief = brief or ""
    found = _find_section(brief, _OBJECTIVE_HEADINGS)
    if found:
        return Extraction(_truncate(found[0]), 'heading "{}"'.format(found[1]))
    imperative = _find_imperative(brief, _OBJECTIVE_IMPERATIVES)
    if imperative:
        return Extraction(_truncate(imperative), "imperative line")
    return _fallback(brief, description)
