# Orchestra Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build `/orchestra`, a local read-only dashboard that reconstructs a Claude Code subagent orchestration from on-disk transcripts and shows the fleet timeline, an evidence-backed handoff graph, per-agent drill-down, and token/health analytics.

**Architecture:** A pure function (`build.py`) turns raw transcript files into a `Run` object; everything else is a thin shell around it. A stdlib HTTP server on `127.0.0.1` serves that object as JSON to a dependency-free single-page frontend that polls every 2 seconds. Nothing is written back to Claude Code's files and nothing leaves the machine.

**Tech Stack:** Python 3.9+ standard library only (`http.server`, `json`, `dataclasses`, `re`, `pathlib`, `unittest`). Frontend is hand-written HTML/CSS/JS with inline SVG — no framework, no build step, no CDN.

**Spec:** `docs/superpowers/specs/2026-09-12-orchestra-design.md`

## Global Constraints

Every task's requirements implicitly include this section. Values are copied verbatim from the spec.

- **Python standard library only.** No pip install, no npm, no build step, at runtime or in tests. Tests use `unittest`, never pytest.
- **Python floor 3.9.** No `match` statements, no `X | Y` type unions at runtime, no `dict[str, int]` builtin generics in annotations that are evaluated. Use `typing.List`, `typing.Optional`, `typing.Dict`.
- **Bind `127.0.0.1` only.** Never `0.0.0.0`.
- **No network egress of any kind.** No CDN, no web fonts, no telemetry, no outbound request. Every asset ships in the package.
- **Read-only.** Never write to any path under `~/.claude`. The only files Orchestra writes are its own portfile (in the system temp directory) and an explicitly requested report file.
- **Tests never read the real `~/.claude` tree.** Every test uses fixtures in a temp directory. The suite must pass on a machine with an empty Claude Code history.
- **All rendered text passes through `redact.scrub`** before serialization.
- **Constants** (from `orchestra/constants.py`, Task 1): `STALL_THRESHOLD_S = 300`, `SESSION_LIVE_THRESHOLD_S = 600`, `HUB_FILE_THRESHOLD = 3`, `HANDOFF_CONTAINMENT = 0.15`, `HANDOFF_RUN_WORDS = 40`, `SHINGLE_SIZE = 8`, `IDLE_SHUTDOWN_S = 1800`, `DEFAULT_PORT = 7717`.
- **Every commit message ends with these two lines:**
  ```
  Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
  Claude-Session: https://claude.ai/code/session_01XTZT9r1dRv9tWmD1xo74k1
  ```
  They are omitted from the commit steps below only to keep the plan readable. Do not omit them in practice.
- **Run the full suite before every commit:** `python -m unittest discover -s tests -t . -v`

---

## File Structure

| File | Responsibility |
|---|---|
| `orchestra/constants.py` | Every tunable threshold. No logic. |
| `orchestra/model.py` | Dataclasses + serialization. No I/O, no parsing. |
| `orchestra/redact.py` | Secret scrubbing. Pure string in, string out. |
| `orchestra/extract.py` | Pull `objective` / `expected_output` out of a brief. Pure. |
| `orchestra/transcript.py` | Incremental JSONL reading with byte offsets. The only file that deals with torn lines and encoding. |
| `orchestra/locate.py` | Find sessions and project directories on disk. The only file that knows `~/.claude`'s layout. |
| `orchestra/build.py` | Assemble `Run` from parsed entries. The intelligence. Pure given file contents. |
| `orchestra/edges.py` | The four edge inferencers + hub collapsing. Pure. |
| `orchestra/report.py` | Render a `Run` to a self-contained HTML file. |
| `orchestra/http.py` | Server, auth, routing. No analysis. |
| `orchestra/__main__.py` | CLI, portfile, detached launch. |
| `orchestra/static/*` | The frontend. |
| `commands/orchestra.md` | The slash command. |

Split by responsibility, not by layer: `edges.py` is separate from `build.py` because the four inferencers are independently testable and will be tuned separately; `transcript.py` is separate from `locate.py` because reading a file and finding a file fail for different reasons and need different tests.

**Divergence from spec §6, deliberate.** The spec listed one `build.py` holding all the intelligence. This plan splits five modules out of it — `constants.py`, `parent.py`, `agentlog.py`, `status.py`, and `service.py` — leaving `build.py` as the assembler that calls them. The reason is testability at the seam that matters: `parent.py` isolates the transcript shapes most likely to drift between Claude Code versions, and `status.py` isolates the state machine most likely to be subtly wrong. A single `build.py` would have made both testable only through a full fixture run. The spec's architecture is otherwise unchanged: `build.py` is still pure given file contents, and it is still where a `Run` comes from.

---

## Task 1: Skeleton, constants, and the data model

**Files:**
- Create: `orchestra/__init__.py`, `orchestra/constants.py`, `orchestra/model.py`
- Create: `tests/__init__.py`, `tests/test_model.py`
- Create: `.gitignore`

**Interfaces:**
- Consumes: nothing.
- Produces: `Agent`, `Edge`, `Batch`, `Run`, `Extraction`, `Round` dataclasses; `Agent.to_light_dict()`, `Agent.to_detail_dict()`, `Run.to_summary_dict()`; status string constants `RUNNING`, `COMPLETED`, `FAILED`, `STALLED`, `ORPHANED`, `UNKNOWN`.

- [ ] **Step 1: Create the package skeleton**

```bash
mkdir -p orchestra/static tests/fixtures
touch orchestra/__init__.py tests/__init__.py
printf '__pycache__/\n*.pyc\norchestra-report-*.html\n' > .gitignore
```

- [ ] **Step 2: Write `orchestra/constants.py`**

```python
"""Every tunable threshold in Orchestra. No logic lives here."""

STALL_THRESHOLD_S = 300
SESSION_LIVE_THRESHOLD_S = 600
HUB_FILE_THRESHOLD = 3
HANDOFF_CONTAINMENT = 0.15
HANDOFF_RUN_WORDS = 40
SHINGLE_SIZE = 8
IDLE_SHUTDOWN_S = 1800
DEFAULT_PORT = 7717

RUNNING = "running"
COMPLETED = "completed"
FAILED = "failed"
STALLED = "stalled"
ORPHANED = "orphaned"
UNKNOWN = "unknown"

ORCHESTRATOR_ID = "main"
```

- [ ] **Step 3: Write the failing test**

Create `tests/test_model.py`:

```python
import unittest

from orchestra.model import Agent, Edge, Extraction, Round, Run
from orchestra import constants as C


def make_agent(**kw):
    defaults = dict(
        agent_id="a1",
        tool_use_id="toolu_1",
        agent_type="general-purpose",
        description="Do the thing",
        model="haiku",
        launch_mode="background",
        brief="A very long brief " * 50,
        result="A very long result " * 50,
    )
    defaults.update(kw)
    return Agent(**defaults)


class TestAgentSerialization(unittest.TestCase):
    def test_light_dict_omits_heavy_fields(self):
        d = make_agent().to_light_dict()
        for heavy in ("brief", "result", "tool_calls"):
            self.assertNotIn(heavy, d)
        self.assertEqual(d["agent_id"], "a1")
        self.assertEqual(d["description"], "Do the thing")

    def test_detail_dict_includes_heavy_fields(self):
        d = make_agent().to_detail_dict()
        self.assertIn("brief", d)
        self.assertIn("result", d)
        self.assertIn("tool_calls", d)

    def test_extraction_source_is_serialized(self):
        a = make_agent(expected_output=Extraction("Return a diff", 'heading "## Deliverable"'))
        d = a.to_detail_dict()
        self.assertEqual(d["expected_output"], "Return a diff")
        self.assertEqual(d["expected_output_source"], 'heading "## Deliverable"')

    def test_duration_spans_first_start_to_last_end(self):
        a = make_agent(rounds=[Round(started_at=100.0, ended_at=150.0),
                               Round(started_at=200.0, ended_at=260.0)])
        self.assertEqual(a.started_at, 100.0)
        self.assertEqual(a.ended_at, 260.0)
        self.assertEqual(a.duration_s, 160.0)

    def test_running_agent_has_no_end(self):
        a = make_agent(rounds=[Round(started_at=100.0, ended_at=None)])
        self.assertIsNone(a.ended_at)
        self.assertIsNone(a.duration_s)


class TestRunSerialization(unittest.TestCase):
    def test_summary_uses_light_agents_and_counts_statuses(self):
        run = Run(
            session_id="s1",
            project_path="E:/p",
            agents=[make_agent(agent_id="a1", status=C.RUNNING),
                    make_agent(agent_id="a2", status=C.COMPLETED),
                    make_agent(agent_id="a3", status=C.FAILED)],
            edges=[Edge(src=C.ORCHESTRATOR_ID, dst="a1", kind="spawn",
                        confidence="exact", evidence={"tool_use_id": "toolu_1"})],
        )
        d = run.to_summary_dict()
        self.assertEqual(d["totals"]["agents"], 3)
        self.assertEqual(d["totals"]["running"], 1)
        self.assertEqual(d["totals"]["completed"], 1)
        self.assertEqual(d["totals"]["failed"], 1)
        self.assertNotIn("brief", d["agents"][0])
        self.assertEqual(d["edges"][0]["kind"], "spawn")


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 4: Run the test to verify it fails**

Run: `python -m unittest discover -s tests -t . -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'orchestra.model'`

- [ ] **Step 5: Write `orchestra/model.py`**

```python
"""Dataclasses for an Orchestra run. No I/O, no parsing."""

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from orchestra import constants as C


@dataclass
class Extraction:
    """A value pulled out of a brief, plus which rule produced it."""
    text: str = ""
    source: str = ""


@dataclass
class Round:
    """One start-to-finish pass of an agent. An agent resumed via SendMessage has several."""
    started_at: Optional[float] = None
    ended_at: Optional[float] = None
    status: str = C.RUNNING
    result: str = ""


@dataclass
class ToolCall:
    name: str = ""
    target: str = ""
    timestamp: Optional[float] = None


@dataclass
class Agent:
    agent_id: str
    tool_use_id: str = ""
    parent_agent_id: Optional[str] = None
    spawn_depth: int = 1
    agent_type: str = ""
    description: str = ""
    model: str = ""
    launch_mode: str = "inline"
    brief: str = ""
    objective: Extraction = field(default_factory=Extraction)
    expected_output: Extraction = field(default_factory=Extraction)
    status: str = C.UNKNOWN
    rounds: List[Round] = field(default_factory=list)
    last_activity_at: Optional[float] = None
    result: str = ""
    tokens: Dict[str, int] = field(default_factory=dict)
    tool_calls: List[ToolCall] = field(default_factory=list)
    files_written: List[str] = field(default_factory=list)
    files_read: List[str] = field(default_factory=list)
    transcript_path: str = ""

    @property
    def started_at(self) -> Optional[float]:
        starts = [r.started_at for r in self.rounds if r.started_at is not None]
        return min(starts) if starts else None

    @property
    def ended_at(self) -> Optional[float]:
        """None while any round is still open — an agent mid-resume has no end."""
        if not self.rounds or any(r.ended_at is None for r in self.rounds):
            return None
        return max(r.ended_at for r in self.rounds)

    @property
    def duration_s(self) -> Optional[float]:
        if self.started_at is None or self.ended_at is None:
            return None
        return self.ended_at - self.started_at

    def to_light_dict(self) -> Dict[str, Any]:
        """Everything the timeline and graph need; nothing large."""
        return {
            "agent_id": self.agent_id,
            "parent_agent_id": self.parent_agent_id,
            "spawn_depth": self.spawn_depth,
            "agent_type": self.agent_type,
            "description": self.description,
            "model": self.model,
            "launch_mode": self.launch_mode,
            "status": self.status,
            "started_at": self.started_at,
            "ended_at": self.ended_at,
            "duration_s": self.duration_s,
            "last_activity_at": self.last_activity_at,
            "tokens": dict(self.tokens),
            "rounds": [{"started_at": r.started_at, "ended_at": r.ended_at,
                        "status": r.status} for r in self.rounds],
            "objective": self.objective.text,
            "files_written_count": len(self.files_written),
            "tool_call_count": len(self.tool_calls),
        }

    def to_detail_dict(self) -> Dict[str, Any]:
        d = self.to_light_dict()
        d.update({
            "brief": self.brief,
            "result": self.result,
            "objective_source": self.objective.source,
            "expected_output": self.expected_output.text,
            "expected_output_source": self.expected_output.source,
            "tool_calls": [{"name": t.name, "target": t.target,
                            "timestamp": t.timestamp} for t in self.tool_calls],
            "files_written": list(self.files_written),
            "files_read": list(self.files_read),
            "transcript_path": self.transcript_path,
        })
        return d


@dataclass
class Edge:
    src: str
    dst: str
    kind: str
    confidence: str
    evidence: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {"src": self.src, "dst": self.dst, "kind": self.kind,
                "confidence": self.confidence, "evidence": dict(self.evidence)}


@dataclass
class Batch:
    """Agents launched in the same assistant turn — one parallel wave."""
    turn_uuid: str = ""
    agent_ids: List[str] = field(default_factory=list)
    launched_at: Optional[float] = None

    def to_dict(self) -> Dict[str, Any]:
        return {"turn_uuid": self.turn_uuid, "agent_ids": list(self.agent_ids),
                "launched_at": self.launched_at}


@dataclass
class HubFile:
    """A file read by many agents but written by none — collapsed out of the graph."""
    path: str = ""
    reader_ids: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {"path": self.path, "reader_ids": list(self.reader_ids)}


@dataclass
class Run:
    session_id: str
    project_path: str = ""
    started_at: Optional[float] = None
    ended_at: Optional[float] = None
    session_live: bool = False
    agents: List[Agent] = field(default_factory=list)
    edges: List[Edge] = field(default_factory=list)
    batches: List[Batch] = field(default_factory=list)
    hub_files: List[HubFile] = field(default_factory=list)
    diagnostics: Dict[str, int] = field(default_factory=dict)

    def agent(self, agent_id: str) -> Optional[Agent]:
        for a in self.agents:
            if a.agent_id == agent_id:
                return a
        return None

    def totals(self) -> Dict[str, Any]:
        counts = {"agents": len(self.agents)}
        for status in (C.RUNNING, C.COMPLETED, C.FAILED, C.STALLED,
                       C.ORPHANED, C.UNKNOWN):
            counts[status] = sum(1 for a in self.agents if a.status == status)
        tokens = {}
        for a in self.agents:
            for k, v in a.tokens.items():
                tokens[k] = tokens.get(k, 0) + v
        counts["tokens"] = tokens
        ends = [a.ended_at for a in self.agents if a.ended_at is not None]
        starts = [a.started_at for a in self.agents if a.started_at is not None]
        counts["wall_time_s"] = (max(ends) - min(starts)) if ends and starts else None
        return counts

    def to_summary_dict(self) -> Dict[str, Any]:
        return {
            "session_id": self.session_id,
            "project_path": self.project_path,
            "started_at": self.started_at,
            "ended_at": self.ended_at,
            "session_live": self.session_live,
            "totals": self.totals(),
            "agents": [a.to_light_dict() for a in self.agents],
            "edges": [e.to_dict() for e in self.edges],
            "batches": [b.to_dict() for b in self.batches],
            "hub_files": [h.to_dict() for h in self.hub_files],
            "diagnostics": dict(self.diagnostics),
        }
```

- [ ] **Step 6: Run the test to verify it passes**

Run: `python -m unittest discover -s tests -t . -v`
Expected: PASS, 6 tests.

- [ ] **Step 7: Commit**

```bash
git add orchestra/ tests/ .gitignore
git commit -m "feat: add Orchestra data model and constants"
```

---

## Task 2: Secret redaction

**Files:**
- Create: `orchestra/redact.py`
- Test: `tests/test_redact.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `scrub(text: str) -> str`, `scrub_obj(obj: Any) -> Any` (recurses through dicts, lists, and strings; leaves other types alone).

- [ ] **Step 1: Write the failing test**

Create `tests/test_redact.py`:

```python
import unittest

from orchestra.redact import scrub, scrub_obj


class TestScrub(unittest.TestCase):
    def test_anthropic_key(self):
        out = scrub("use <fake-anthropic-key> now")
        self.assertNotIn("AAAABBBB", out)
        self.assertIn("redacted:anthropic_key", out)

    def test_github_token(self):
        out = scrub("token <fake-github-token>")
        self.assertIn("redacted:github_token", out)
        self.assertNotIn("ABCDEFGHIJ", out)

    def test_aws_access_key_id(self):
        self.assertIn("redacted:aws_key_id", scrub("<fake-aws-key-id>"))

    def test_bearer_header(self):
        out = scrub("Authorization: Bearer abcdefghijklmnopqrstuvwxyz123456")
        self.assertIn("redacted:bearer", out)

    def test_jwt(self):
        jwt = "<fake-jwt>"
        self.assertIn("redacted:jwt", scrub(jwt))

    def test_private_key_block(self):
        pem = "<fake-pem-block>"
        out = scrub(pem)
        self.assertIn("redacted:private_key", out)
        self.assertNotIn("MIIEpAIBAAKC", out)

    def test_assignment_keeps_the_key_name(self):
        out = scrub('password="hunter2hunter2"')
        self.assertIn("password", out)
        self.assertNotIn("hunter2hunter2", out)

    def test_ordinary_prose_is_untouched(self):
        text = "Read src/main.py and return a summary of the sk-learn usage."
        self.assertEqual(scrub(text), text)

    def test_empty_and_none_safe(self):
        self.assertEqual(scrub(""), "")
        self.assertIsNone(scrub(None))


class TestScrubObj(unittest.TestCase):
    def test_recurses_nested_structures(self):
        obj = {"a": ["<fake-github-token>", {"b": "clean"}], "n": 5}
        out = scrub_obj(obj)
        self.assertIn("redacted:github_token", out["a"][0])
        self.assertEqual(out["a"][1]["b"], "clean")
        self.assertEqual(out["n"], 5)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `python -m unittest tests.test_redact -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'orchestra.redact'`

- [ ] **Step 3: Write `orchestra/redact.py`**

```python
"""Secret scrubbing. Applied at the serialization boundary so no path bypasses it."""

import re
from typing import Any, List, Optional, Pattern, Tuple

_MARK = "<redacted:{}>"

# Order matters: the more specific pattern must come first, or the generic one
# eats its prefix and the label is wrong.
_PATTERNS: List[Tuple[str, Pattern]] = [
    ("private_key", re.compile(
        r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----",
        re.DOTALL)),
    ("jwt", re.compile(r"\beyJ[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}")),
    ("anthropic_key", re.compile(r"\bsk-ant-[A-Za-z0-9_\-]{16,}")),
    ("openai_key", re.compile(r"\bsk-[A-Za-z0-9]{20,}")),
    ("github_token", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}")),
    ("aws_key_id", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("bearer", re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._\-]{20,}")),
]

# Handled separately: only the value is replaced, so the key name stays readable.
_ASSIGNMENT = re.compile(
    r"(?i)\b(password|passwd|secret|api[_-]?key|access[_-]?token|auth[_-]?token)"
    r"(\s*[=:]\s*)(['\"]?)([^\s'\"]{8,})(['\"]?)")


def _assignment_repl(m: "re.Match") -> str:
    return "{}{}{}{}{}".format(m.group(1), m.group(2), m.group(3),
                               _MARK.format("secret"), m.group(5))


def scrub(text: Optional[str]) -> Optional[str]:
    """Replace anything that looks like a credential with a labelled marker."""
    if not text:
        return text
    for label, pattern in _PATTERNS:
        text = pattern.sub(_MARK.format(label), text)
    return _ASSIGNMENT.sub(_assignment_repl, text)


def scrub_obj(obj: Any) -> Any:
    """Recurse through dicts, lists, and strings. Other types pass through."""
    if isinstance(obj, str):
        return scrub(obj)
    if isinstance(obj, dict):
        return {k: scrub_obj(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [scrub_obj(v) for v in obj]
    return obj
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `python -m unittest tests.test_redact -v`
Expected: PASS, 10 tests.

Note on `test_ordinary_prose_is_untouched`: `sk-learn` survives because `openai_key` requires 20+ alphanumerics after `sk-`. If that test fails, the pattern was loosened — tighten it back rather than changing the test.

- [ ] **Step 5: Commit**

```bash
git add orchestra/redact.py tests/test_redact.py
git commit -m "feat: add secret redaction with labelled markers"
```

---

## Task 3: Objective and expected-output extraction

**Files:**
- Create: `orchestra/extract.py`
- Test: `tests/test_extract.py`

**Interfaces:**
- Consumes: `Extraction` from `orchestra.model` (Task 1).
- Produces: `extract_objective(brief: str, description: str) -> Extraction`, `extract_expected_output(brief: str, description: str) -> Extraction`.

Three tiers, first match wins. The tier that fired is recorded in `Extraction.source` and shown in the UI, so the tool never claims precision it does not have.

- [ ] **Step 1: Write the failing test**

Create `tests/test_extract.py`:

```python
import unittest

from orchestra.extract import extract_expected_output, extract_objective


BRIEF_WITH_HEADING = """You are implementing Task 4.

## Context

Some background about the repo.

## Deliverable

A passing test suite and a commit.
Report the commit SHA.

## Notes

Ignore the linter.
"""

BRIEF_WITH_IMPERATIVE = """You are reviewing a diff.

Look at the changes carefully.
Return a list of findings, most severe first.
Do not fix anything.
"""

BRIEF_PLAIN = """Investigate why the build is slow on Windows and figure out
what the biggest contributor is.

Then keep going.
"""


class TestExpectedOutput(unittest.TestCase):
    def test_tier1_heading_wins(self):
        e = extract_expected_output(BRIEF_WITH_HEADING, "Implement task 4")
        self.assertIn("passing test suite", e.text)
        self.assertIn("Report the commit SHA", e.text)
        self.assertNotIn("Ignore the linter", e.text)
        self.assertIn("Deliverable", e.source)

    def test_tier1_is_case_insensitive_and_level_agnostic(self):
        brief = "# Job\n\n### expected output\n\nA JSON blob.\n"
        e = extract_expected_output(brief, "")
        self.assertEqual(e.text.strip(), "A JSON blob.")

    def test_tier1_section_ends_at_same_or_higher_heading(self):
        brief = "## Output\n\nThe answer.\n\n### Sub\n\nStill the answer.\n\n## Other\n\nNope.\n"
        e = extract_expected_output(brief, "")
        self.assertIn("Still the answer", e.text)
        self.assertNotIn("Nope", e.text)

    def test_tier2_imperative_line(self):
        e = extract_expected_output(BRIEF_WITH_IMPERATIVE, "Review the diff")
        self.assertIn("Return a list of findings", e.text)
        self.assertEqual(e.source, "imperative line")

    def test_tier3_fallback_uses_description_and_first_paragraph(self):
        e = extract_expected_output(BRIEF_PLAIN, "Investigate build speed")
        self.assertIn("Investigate build speed", e.text)
        self.assertEqual(e.source, "fallback")

    def test_empty_brief_is_safe(self):
        e = extract_expected_output("", "")
        self.assertEqual(e.text, "")
        self.assertEqual(e.source, "none")


class TestObjective(unittest.TestCase):
    def test_objective_heading(self):
        brief = "## Objective\n\nMake the tests pass.\n\n## Deliverable\n\nA commit.\n"
        e = extract_objective(brief, "")
        self.assertEqual(e.text.strip(), "Make the tests pass.")

    def test_objective_you_are_line(self):
        e = extract_objective(BRIEF_WITH_IMPERATIVE, "")
        self.assertIn("reviewing a diff", e.text)

    def test_objective_falls_back_to_description(self):
        e = extract_objective("Some text with no signals at all.", "Fix the parser")
        self.assertIn("Fix the parser", e.text)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `python -m unittest tests.test_extract -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'orchestra.extract'`

- [ ] **Step 3: Write `orchestra/extract.py`**

```python
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
    return _fallback(brief, description)


def extract_objective(brief: str, description: str = "") -> Extraction:
    brief = brief or ""
    found = _find_section(brief, _OBJECTIVE_HEADINGS)
    if found:
        return Extraction(_truncate(found[0]), 'heading "{}"'.format(found[1]))
    imperative = _find_imperative(brief, _OBJECTIVE_IMPERATIVES)
    if imperative:
        return Extraction(_truncate(imperative), "imperative line")
    return _fallback(brief, description)
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `python -m unittest tests.test_extract -v`
Expected: PASS, 9 tests.

- [ ] **Step 5: Commit**

```bash
git add orchestra/extract.py tests/test_extract.py
git commit -m "feat: add tiered objective and expected-output extraction"
```

---

## Task 4: Incremental transcript reading

**Files:**
- Create: `orchestra/transcript.py`
- Test: `tests/test_transcript.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `IncrementalReader` with `read_new(path) -> List[dict]`, `read_json(path) -> Optional[dict]`, and a `diagnostics` dict carrying `unparsable_lines` and `torn_reads`.

This is the only module that deals with partial writes and encoding. Transcripts are appended to while Orchestra reads them, so a torn final line is the normal case, not an exception.

- [ ] **Step 1: Write the failing test**

Create `tests/test_transcript.py`:

```python
import json
import os
import tempfile
import unittest

from orchestra.transcript import IncrementalReader


class TranscriptTestCase(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.path = os.path.join(self.dir, "session.jsonl")
        self.reader = IncrementalReader()

    def write(self, text, mode="ab"):
        with open(self.path, mode) as fh:
            fh.write(text.encode("utf-8"))

    def line(self, **kw):
        return json.dumps(kw) + "\n"


class TestIncrementalRead(TranscriptTestCase):
    def test_reads_all_lines_on_first_pass(self):
        self.write(self.line(a=1) + self.line(a=2))
        entries = self.reader.read_new(self.path)
        self.assertEqual([e["a"] for e in entries], [1, 2])

    def test_second_pass_returns_only_new_lines(self):
        self.write(self.line(a=1))
        self.reader.read_new(self.path)
        self.write(self.line(a=2))
        entries = self.reader.read_new(self.path)
        self.assertEqual([e["a"] for e in entries], [2])

    def test_no_new_data_returns_empty(self):
        self.write(self.line(a=1))
        self.reader.read_new(self.path)
        self.assertEqual(self.reader.read_new(self.path), [])

    def test_torn_final_line_is_not_consumed_and_is_read_whole_next_time(self):
        complete = self.line(a=1)
        torn = '{"a": 2, "b": "half'
        self.write(complete + torn)
        first = self.reader.read_new(self.path)
        self.assertEqual([e["a"] for e in first], [1])
        self.assertEqual(self.reader.diagnostics["torn_reads"], 1)

        self.write('way"}\n')
        second = self.reader.read_new(self.path)
        self.assertEqual([e["a"] for e in second], [2])
        self.assertEqual(second[0]["b"], "halfway")

    def test_unparsable_complete_line_is_counted_and_skipped(self):
        self.write(self.line(a=1) + "this is not json\n" + self.line(a=3))
        entries = self.reader.read_new(self.path)
        self.assertEqual([e["a"] for e in entries], [1, 3])
        self.assertEqual(self.reader.diagnostics["unparsable_lines"], 1)

    def test_invalid_utf8_does_not_raise(self):
        with open(self.path, "wb") as fh:
            fh.write(b'{"a": "caf\xe9"}\n')
        entries = self.reader.read_new(self.path)
        self.assertEqual(len(entries), 1)

    def test_truncated_file_resets_offset(self):
        self.write(self.line(a=1) + self.line(a=2))
        self.reader.read_new(self.path)
        self.write(self.line(a=9), mode="wb")
        entries = self.reader.read_new(self.path)
        self.assertEqual([e["a"] for e in entries], [9])

    def test_missing_file_returns_empty(self):
        self.assertEqual(self.reader.read_new(os.path.join(self.dir, "nope.jsonl")), [])


class TestReadJson(TranscriptTestCase):
    def test_reads_a_meta_file(self):
        p = os.path.join(self.dir, "agent.meta.json")
        with open(p, "w") as fh:
            json.dump({"agentType": "general-purpose"}, fh)
        self.assertEqual(self.reader.read_json(p)["agentType"], "general-purpose")

    def test_missing_or_broken_returns_none(self):
        self.assertIsNone(self.reader.read_json(os.path.join(self.dir, "nope.json")))
        bad = os.path.join(self.dir, "bad.json")
        with open(bad, "w") as fh:
            fh.write("{not json")
        self.assertIsNone(self.reader.read_json(bad))


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `python -m unittest tests.test_transcript -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'orchestra.transcript'`

- [ ] **Step 3: Write `orchestra/transcript.py`**

```python
"""Incremental JSONL reading.

Claude Code appends to these files while we read them, so a torn final line is
the normal case. The byte offset is never advanced past an incomplete line;
the next poll re-reads it whole.
"""

import json
import os
from typing import Any, Dict, List, Optional


class IncrementalReader:
    """Remembers how far into each file it has read. Reuse one per server."""

    def __init__(self) -> None:
        self._offsets: Dict[str, int] = {}
        self.diagnostics: Dict[str, int] = {"unparsable_lines": 0, "torn_reads": 0}

    def reset(self, path: Optional[str] = None) -> None:
        if path is None:
            self._offsets.clear()
        else:
            self._offsets.pop(os.path.normcase(path), None)

    def read_new(self, path: str) -> List[Dict[str, Any]]:
        """Return the entries appended since the last call. Never raises."""
        key = os.path.normcase(path)
        try:
            size = os.path.getsize(path)
        except OSError:
            return []

        offset = self._offsets.get(key, 0)
        if size < offset:
            # File was truncated or replaced — start over rather than read garbage.
            offset = 0
        if size == offset:
            return []

        try:
            with open(path, "rb") as fh:
                fh.seek(offset)
                chunk = fh.read()
        except OSError:
            return []

        # Only whole lines are consumed. If the chunk does not end in a newline,
        # the tail is a partial write: leave it for next time.
        last_newline = chunk.rfind(b"\n")
        if last_newline == -1:
            self.diagnostics["torn_reads"] += 1
            return []
        if last_newline != len(chunk) - 1:
            self.diagnostics["torn_reads"] += 1
        consumable = chunk[:last_newline + 1]
        self._offsets[key] = offset + len(consumable)

        entries: List[Dict[str, Any]] = []
        for raw in consumable.decode("utf-8", errors="replace").splitlines():
            if not raw.strip():
                continue
            try:
                parsed = json.loads(raw)
            except ValueError:
                self.diagnostics["unparsable_lines"] += 1
                continue
            if isinstance(parsed, dict):
                entries.append(parsed)
            else:
                self.diagnostics["unparsable_lines"] += 1
        return entries

    def read_json(self, path: str) -> Optional[Dict[str, Any]]:
        """Read a whole small JSON file (an agent .meta.json). None on any problem."""
        try:
            with open(path, "rb") as fh:
                parsed = json.loads(fh.read().decode("utf-8", errors="replace"))
        except (OSError, ValueError):
            return None
        return parsed if isinstance(parsed, dict) else None
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `python -m unittest tests.test_transcript -v`
Expected: PASS, 10 tests.

- [ ] **Step 5: Commit**

```bash
git add orchestra/transcript.py tests/test_transcript.py
git commit -m "feat: add incremental JSONL reader tolerant of partial writes"
```

---

## Task 5: Locating sessions on disk

**Files:**
- Create: `orchestra/locate.py`
- Test: `tests/test_locate.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `encode_project_dir(path: str) -> str`; `SessionPaths` (fields `session_id`, `session_jsonl`, `subagents_dir`, `project_dir`); `find_session(session_id: str, root: str) -> Optional[SessionPaths]`; `list_sessions(project_dir: str) -> List[SessionInfo]`; `SessionInfo` (fields `session_id`, `modified_at`, `agent_count`, `size_bytes`); `claude_root() -> str`.

The primary lookup globs for `<session_id>.jsonl` under every project directory, so a session is found without depending on the directory-name encoding at all. `encode_project_dir` exists only for the case where no session id is available.

- [ ] **Step 1: Write the failing test**

Create `tests/test_locate.py`:

```python
import os
import tempfile
import unittest

from orchestra.locate import (SessionPaths, encode_project_dir, find_session,
                              list_sessions)


class TestEncodeProjectDir(unittest.TestCase):
    def test_windows_path(self):
        self.assertEqual(encode_project_dir(r"E:\god_ai\claude-SA"),
                         "E--god-ai-claude-SA")

    def test_windows_user_path(self):
        self.assertEqual(encode_project_dir(r"C:\Users\Chandan"), "C--Users-Chandan")

    def test_posix_path(self):
        self.assertEqual(encode_project_dir("/home/dev/my_app"), "-home-dev-my-app")

    def test_existing_dashes_survive(self):
        self.assertEqual(encode_project_dir(r"E:\project-boss"), "E--project-boss")


class LocateTestCase(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp()
        self.projects = os.path.join(self.root, "projects")
        os.makedirs(self.projects)

    def make_session(self, project, session_id, agents=0):
        pdir = os.path.join(self.projects, project)
        os.makedirs(pdir, exist_ok=True)
        with open(os.path.join(pdir, session_id + ".jsonl"), "w") as fh:
            fh.write('{"type": "user"}\n')
        if agents:
            sub = os.path.join(pdir, session_id, "subagents")
            os.makedirs(sub, exist_ok=True)
            for i in range(agents):
                base = os.path.join(sub, "agent-a{}".format(i))
                open(base + ".jsonl", "w").close()
                open(base + ".meta.json", "w").close()
        return pdir


class TestFindSession(LocateTestCase):
    def test_finds_session_in_any_project(self):
        self.make_session("E--god-ai-claude-SA", "sess-1", agents=2)
        found = find_session("sess-1", root=self.root)
        self.assertIsInstance(found, SessionPaths)
        self.assertTrue(found.session_jsonl.endswith("sess-1.jsonl"))
        self.assertTrue(found.subagents_dir.endswith(os.path.join("sess-1", "subagents")))
        self.assertTrue(os.path.isdir(found.subagents_dir))

    def test_missing_session_returns_none(self):
        self.make_session("E--p", "sess-1")
        self.assertIsNone(find_session("nope", root=self.root))

    def test_session_without_subagents_dir_still_resolves(self):
        self.make_session("E--p", "sess-2", agents=0)
        found = find_session("sess-2", root=self.root)
        self.assertIsNotNone(found)
        self.assertFalse(os.path.isdir(found.subagents_dir))

    def test_missing_root_returns_none(self):
        self.assertIsNone(find_session("sess-1", root=os.path.join(self.root, "gone")))


class TestListSessions(LocateTestCase):
    def test_lists_newest_first_with_agent_counts(self):
        pdir = self.make_session("E--p", "old", agents=1)
        self.make_session("E--p", "new", agents=3)
        os.utime(os.path.join(pdir, "old.jsonl"), (1000, 1000))
        sessions = list_sessions(pdir)
        self.assertEqual([s.session_id for s in sessions], ["new", "old"])
        self.assertEqual(sessions[0].agent_count, 3)
        self.assertEqual(sessions[1].agent_count, 1)

    def test_missing_dir_returns_empty(self):
        self.assertEqual(list_sessions(os.path.join(self.root, "gone")), [])


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `python -m unittest tests.test_locate -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'orchestra.locate'`

- [ ] **Step 3: Write `orchestra/locate.py`**

```python
"""Finding Claude Code sessions on disk. The only module that knows the layout."""

import glob
import os
import re
from dataclasses import dataclass
from typing import List, Optional


@dataclass
class SessionPaths:
    session_id: str
    session_jsonl: str
    subagents_dir: str
    project_dir: str


@dataclass
class SessionInfo:
    session_id: str
    modified_at: float
    agent_count: int
    size_bytes: int


def claude_root() -> str:
    """The ~/.claude directory. Honours CLAUDE_CONFIG_DIR when set."""
    override = os.environ.get("CLAUDE_CONFIG_DIR")
    if override:
        return override
    return os.path.join(os.path.expanduser("~"), ".claude")


def encode_project_dir(path: str) -> str:
    """Claude Code's project-directory name: every non-alphanumeric becomes a dash."""
    return re.sub(r"[^A-Za-z0-9]", "-", path)


def _paths_for(session_jsonl: str, session_id: str) -> SessionPaths:
    project_dir = os.path.dirname(session_jsonl)
    return SessionPaths(
        session_id=session_id,
        session_jsonl=session_jsonl,
        subagents_dir=os.path.join(project_dir, session_id, "subagents"),
        project_dir=project_dir,
    )


def find_session(session_id: str, root: Optional[str] = None) -> Optional[SessionPaths]:
    """Locate a session by id across every project. Encoding-independent."""
    if not session_id:
        return None
    base = os.path.join(root or claude_root(), "projects")
    if not os.path.isdir(base):
        return None
    pattern = os.path.join(base, "*", session_id + ".jsonl")
    matches = sorted(glob.glob(pattern))
    if not matches:
        return None
    return _paths_for(matches[0], session_id)


def find_project_dir(cwd: str, root: Optional[str] = None) -> Optional[str]:
    """Fallback when no session id is available."""
    base = os.path.join(root or claude_root(), "projects")
    candidate = os.path.join(base, encode_project_dir(os.path.abspath(cwd)))
    return candidate if os.path.isdir(candidate) else None


def list_sessions(project_dir: str) -> List[SessionInfo]:
    """Every session in a project, newest first."""
    if not os.path.isdir(project_dir):
        return []
    out: List[SessionInfo] = []
    for path in glob.glob(os.path.join(project_dir, "*.jsonl")):
        session_id = os.path.basename(path)[: -len(".jsonl")]
        subagents = os.path.join(project_dir, session_id, "subagents")
        agent_count = 0
        if os.path.isdir(subagents):
            agent_count = len(glob.glob(os.path.join(subagents, "agent-*.meta.json")))
        try:
            stat = os.stat(path)
        except OSError:
            continue
        out.append(SessionInfo(session_id=session_id, modified_at=stat.st_mtime,
                               agent_count=agent_count, size_bytes=stat.st_size))
    out.sort(key=lambda s: s.modified_at, reverse=True)
    return out
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `python -m unittest tests.test_locate -v`
Expected: PASS, 10 tests.

- [ ] **Step 5: Commit**

```bash
git add orchestra/locate.py tests/test_locate.py
git commit -m "feat: locate sessions by id independently of path encoding"
```

---

## Task 6: Parsing the parent transcript

**Files:**
- Create: `orchestra/parent.py`
- Test: `tests/test_parent.py`

**Interfaces:**
- Consumes: nothing (operates on already-parsed entry dicts from Task 4).
- Produces: `LaunchRecord` (fields `tool_use_id`, `description`, `prompt`, `model`, `launched_at`, `turn_uuid`, `launcher_agent_id`); `ResultRecord` (fields `tool_use_id`, `agent_id`, `launch_mode`, `inline_result`, `is_error`, `at`); `Notification` (fields `agent_id`, `tool_use_id`, `status`, `result`, `at`); `ParentIndex` with `.ingest(entries)` and attributes `launches: Dict[str, LaunchRecord]`, `results: Dict[str, ResultRecord]`, `notifications: Dict[str, List[Notification]]`, `cwd: str`, `last_entry_at: float`.

This is split out of `build.py` because parent-transcript shapes are the most likely thing to drift between Claude Code versions, and isolating them means drift breaks one small, heavily-tested module.

- [ ] **Step 1: Write the failing test**

Create `tests/test_parent.py`:

```python
import datetime
import unittest

from orchestra.parent import ParentIndex


def utc(year, month, day, hour, minute, second, frac=0.0):
    """Expected epoch, computed with datetime rather than the code under test.

    Using a hand-written constant here hides local-time bugs and gets typo'd;
    computing it a different way than parent.py does still catches a
    timegm-vs-mktime mistake.
    """
    moment = datetime.datetime(year, month, day, hour, minute, second,
                               tzinfo=datetime.timezone.utc)
    return moment.timestamp() + frac


TS1 = "2026-09-09T04:57:11.912Z"
TS2 = "2026-09-09T04:57:14.245Z"
TS3 = "2026-09-09T04:59:50.984Z"

LAUNCH = {
    "uuid": "turn-1", "timestamp": TS1, "type": "assistant",
    "cwd": r"E:\god_ai\dev-token-dashboard",
    "message": {"role": "assistant", "content": [
        {"type": "tool_use", "id": "toolu_1", "name": "Agent",
         "input": {"description": "Implement Task 1", "model": "haiku",
                   "prompt": "You are implementing Task 1..."}}]}}

BACKGROUND_RESULT = {
    "uuid": "r-1", "timestamp": TS2, "type": "user",
    "message": {"role": "user", "content": [
        {"type": "tool_result", "tool_use_id": "toolu_1", "content": [
            {"type": "text", "text": "Async agent launched successfully.\n"
                                     "agentId: ad434e54374f9fc8b (internal ID)\n"}]}]}}

NOTIFICATION = {
    "uuid": "n-1", "timestamp": TS3, "type": "user",
    "message": {"role": "user", "content":
        "<task-notification>\n<task-id>ad434e54374f9fc8b</task-id>\n"
        "<tool-use-id>toolu_1</tool-use-id>\n<status>completed</status>\n"
        "<summary>Agent finished</summary>\n<result>## Summary\n\nDONE</result>\n"
        "</task-notification>"}}

INLINE_LAUNCH = {
    "uuid": "turn-2", "timestamp": TS1, "type": "assistant",
    "message": {"role": "assistant", "content": [
        {"type": "tool_use", "id": "toolu_2", "name": "Task",
         "input": {"description": "Search the repo", "prompt": "Find all callers."}}]}}

INLINE_RESULT = {
    "uuid": "r-2", "timestamp": TS2, "type": "user",
    "message": {"role": "user", "content": [
        {"type": "tool_result", "tool_use_id": "toolu_2",
         "content": [{"type": "text", "text": "Found 3 callers in src/."}]}]}}


class TestParentIndex(unittest.TestCase):
    def test_captures_launch_fields(self):
        idx = ParentIndex()
        idx.ingest([LAUNCH])
        launch = idx.launches["toolu_1"]
        self.assertEqual(launch.description, "Implement Task 1")
        self.assertEqual(launch.model, "haiku")
        self.assertIn("implementing Task 1", launch.prompt)
        self.assertEqual(launch.turn_uuid, "turn-1")
        self.assertAlmostEqual(launch.launched_at,
                               utc(2026, 9, 9, 4, 57, 11, 0.912), places=2)

    def test_captures_cwd_and_last_entry_time(self):
        idx = ParentIndex()
        idx.ingest([LAUNCH, BACKGROUND_RESULT])
        self.assertEqual(idx.cwd, r"E:\god_ai\dev-token-dashboard")
        self.assertGreater(idx.last_entry_at, 0)

    def test_background_result_extracts_agent_id(self):
        idx = ParentIndex()
        idx.ingest([LAUNCH, BACKGROUND_RESULT])
        result = idx.results["toolu_1"]
        self.assertEqual(result.agent_id, "ad434e54374f9fc8b")
        self.assertEqual(result.launch_mode, "background")
        self.assertEqual(result.inline_result, "")

    def test_inline_result_is_the_final_output(self):
        idx = ParentIndex()
        idx.ingest([INLINE_LAUNCH, INLINE_RESULT])
        result = idx.results["toolu_2"]
        self.assertEqual(result.launch_mode, "inline")
        self.assertEqual(result.inline_result, "Found 3 callers in src/.")
        self.assertEqual(result.agent_id, "")

    def test_error_result_is_flagged(self):
        entry = dict(INLINE_RESULT)
        entry["message"] = {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": "toolu_2", "is_error": True,
             "content": "Agent failed to start"}]}
        idx = ParentIndex()
        idx.ingest([INLINE_LAUNCH, entry])
        self.assertTrue(idx.results["toolu_2"].is_error)

    def test_notification_is_parsed(self):
        idx = ParentIndex()
        idx.ingest([LAUNCH, BACKGROUND_RESULT, NOTIFICATION])
        notes = idx.notifications["ad434e54374f9fc8b"]
        self.assertEqual(len(notes), 1)
        self.assertEqual(notes[0].status, "completed")
        self.assertEqual(notes[0].tool_use_id, "toolu_1")
        self.assertIn("DONE", notes[0].result)

    def test_repeated_notifications_accumulate_in_order(self):
        second = dict(NOTIFICATION)
        second["uuid"] = "n-2"
        second["timestamp"] = "2026-09-09T05:10:00.000Z"
        second["message"] = {"role": "user", "content":
            NOTIFICATION["message"]["content"].replace("DONE", "DONE AGAIN")}
        idx = ParentIndex()
        idx.ingest([NOTIFICATION, second])
        notes = idx.notifications["ad434e54374f9fc8b"]
        self.assertEqual(len(notes), 2)
        self.assertIn("DONE AGAIN", notes[1].result)
        self.assertLess(notes[0].at, notes[1].at)

    def test_non_agent_tool_uses_are_ignored(self):
        entry = {"uuid": "t", "timestamp": TS1, "type": "assistant",
                 "message": {"role": "assistant", "content": [
                     {"type": "tool_use", "id": "toolu_9", "name": "Bash",
                      "input": {"command": "ls"}}]}}
        idx = ParentIndex()
        idx.ingest([entry])
        self.assertEqual(idx.launches, {})

    def test_ingest_is_incremental(self):
        idx = ParentIndex()
        idx.ingest([LAUNCH])
        idx.ingest([BACKGROUND_RESULT])
        self.assertIn("toolu_1", idx.launches)
        self.assertIn("toolu_1", idx.results)

    def test_malformed_entries_do_not_raise(self):
        idx = ParentIndex()
        idx.ingest([{}, {"message": None}, {"message": {"content": "plain string"}},
                    {"message": {"content": [None, 5, {"type": "tool_use"}]}}])
        self.assertEqual(idx.launches, {})


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `python -m unittest tests.test_parent -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'orchestra.parent'`

- [ ] **Step 3: Write `orchestra/parent.py`**

```python
"""Parsing the orchestrator's transcript: launches, results, and notifications.

Isolated from build.py because these shapes are the most likely thing to change
between Claude Code versions. Every accessor tolerates missing and unexpected
fields — a format change should degrade the view, not crash it.
"""

import calendar
import re
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

AGENT_TOOL_NAMES = ("Agent", "Task")

_AGENT_ID_RE = re.compile(r"agentId:\s*([0-9a-f]{8,})")
_NOTIFICATION_RE = re.compile(r"<task-notification>(.*?)</task-notification>", re.DOTALL)
_BACKGROUND_MARKER = "Async agent launched"


def _tag(block: str, name: str) -> str:
    m = re.search(r"<{0}>(.*?)</{0}>".format(name), block, re.DOTALL)
    return m.group(1).strip() if m else ""


def parse_timestamp(value: Any) -> Optional[float]:
    """ISO-8601 with a trailing Z to a POSIX float. None if unparsable."""
    if not isinstance(value, str) or not value:
        return None
    text = value.rstrip("Z")
    frac = 0.0
    if "." in text:
        text, _, frac_text = text.partition(".")
        try:
            frac = float("0." + frac_text)
        except ValueError:
            frac = 0.0
    try:
        parsed = time.strptime(text, "%Y-%m-%dT%H:%M:%S")
    except ValueError:
        return None
    return calendar.timegm(parsed) + frac


def _content_blocks(entry: Dict[str, Any]) -> List[Dict[str, Any]]:
    message = entry.get("message")
    if not isinstance(message, dict):
        return []
    content = message.get("content")
    if not isinstance(content, list):
        return []
    return [b for b in content if isinstance(b, dict)]


def _content_text(entry: Dict[str, Any]) -> str:
    message = entry.get("message")
    if not isinstance(message, dict):
        return ""
    content = message.get("content")
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    parts = []
    for block in content:
        if isinstance(block, dict) and block.get("type") == "text":
            parts.append(str(block.get("text", "")))
    return "\n".join(parts)


def _result_text(block: Dict[str, Any]) -> str:
    content = block.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, dict) and item.get("type") == "text":
                parts.append(str(item.get("text", "")))
        return "\n".join(parts)
    return ""


@dataclass
class LaunchRecord:
    tool_use_id: str
    description: str = ""
    prompt: str = ""
    model: str = ""
    launched_at: Optional[float] = None
    turn_uuid: str = ""
    launcher_agent_id: Optional[str] = None


@dataclass
class ResultRecord:
    tool_use_id: str
    agent_id: str = ""
    launch_mode: str = "inline"
    inline_result: str = ""
    is_error: bool = False
    at: Optional[float] = None


@dataclass
class Notification:
    agent_id: str
    tool_use_id: str = ""
    status: str = ""
    result: str = ""
    summary: str = ""
    at: Optional[float] = None


@dataclass
class ParentIndex:
    """Accumulates launches, results, and notifications across incremental reads."""
    launches: Dict[str, LaunchRecord] = field(default_factory=dict)
    results: Dict[str, ResultRecord] = field(default_factory=dict)
    notifications: Dict[str, List[Notification]] = field(default_factory=dict)
    cwd: str = ""
    last_entry_at: float = 0.0

    def ingest(self, entries: List[Dict[str, Any]]) -> None:
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            at = parse_timestamp(entry.get("timestamp"))
            if at:
                self.last_entry_at = max(self.last_entry_at, at)
            if not self.cwd and isinstance(entry.get("cwd"), str):
                self.cwd = entry["cwd"]
            self._ingest_blocks(entry, at)
            self._ingest_notifications(entry, at)

    def _ingest_blocks(self, entry: Dict[str, Any], at: Optional[float]) -> None:
        launcher = entry.get("agentId") if entry.get("isSidechain") else None
        for block in _content_blocks(entry):
            kind = block.get("type")
            if kind == "tool_use" and block.get("name") in AGENT_TOOL_NAMES:
                params = block.get("input") if isinstance(block.get("input"), dict) else {}
                tool_use_id = str(block.get("id", ""))
                if not tool_use_id:
                    continue
                self.launches[tool_use_id] = LaunchRecord(
                    tool_use_id=tool_use_id,
                    description=str(params.get("description", "")),
                    prompt=str(params.get("prompt", "")),
                    model=str(params.get("model", "")),
                    launched_at=at,
                    turn_uuid=str(entry.get("uuid", "")),
                    launcher_agent_id=launcher,
                )
            elif kind == "tool_result":
                tool_use_id = str(block.get("tool_use_id", ""))
                if not tool_use_id:
                    continue
                text = _result_text(block)
                is_background = _BACKGROUND_MARKER in text
                match = _AGENT_ID_RE.search(text)
                self.results[tool_use_id] = ResultRecord(
                    tool_use_id=tool_use_id,
                    agent_id=match.group(1) if match else "",
                    launch_mode="background" if is_background else "inline",
                    inline_result="" if is_background else text.strip(),
                    is_error=bool(block.get("is_error")),
                    at=at,
                )

    def _ingest_notifications(self, entry: Dict[str, Any], at: Optional[float]) -> None:
        text = _content_text(entry)
        if "<task-notification>" not in text:
            return
        for body in _NOTIFICATION_RE.findall(text):
            agent_id = _tag(body, "task-id")
            if not agent_id:
                continue
            note = Notification(
                agent_id=agent_id,
                tool_use_id=_tag(body, "tool-use-id"),
                status=_tag(body, "status"),
                result=_tag(body, "result"),
                summary=_tag(body, "summary"),
                at=at,
            )
            self.notifications.setdefault(agent_id, []).append(note)
        for notes in self.notifications.values():
            notes.sort(key=lambda n: (n.at is None, n.at))
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `python -m unittest tests.test_parent -v`
Expected: PASS, 10 tests.

If `test_captures_launch_fields` fails on the timestamp assertion, check that `parse_timestamp` is using `calendar.timegm` (UTC) and not `time.mktime` (local time) — the transcripts are UTC and a local-time conversion silently shifts every duration in the dashboard by the machine's offset.

- [ ] **Step 5: Commit**

```bash
git add orchestra/parent.py tests/test_parent.py
git commit -m "feat: parse launches, results, and task notifications from parent transcript"
```

---

## Task 7: Digesting an agent's own transcript

**Files:**
- Create: `orchestra/agentlog.py`
- Test: `tests/test_agentlog.py`

**Interfaces:**
- Consumes: `parse_timestamp`, `_content_blocks` from `orchestra.parent` (Task 6) — import `parse_timestamp` only; re-implement block access locally via the exported helper `content_blocks`, which Task 6's module must expose. **Add `content_blocks = _content_blocks` as a module-level alias at the bottom of `orchestra/parent.py` as part of this task.**
- Produces: `AgentDigest` (fields `tokens`, `tool_calls`, `files_written`, `files_read`, `last_activity_at`, `final_text`, `ended_mid_tool`); `AgentDigest.ingest(entries)`.

- [ ] **Step 1: Add the alias to `orchestra/parent.py`**

Append at the end of `orchestra/parent.py`:

```python
content_blocks = _content_blocks
content_text = _content_text
```

- [ ] **Step 2: Write the failing test**

Create `tests/test_agentlog.py`:

```python
import unittest

from orchestra.agentlog import AgentDigest

TS = "2026-09-09T05:00:00.000Z"
TS_LATER = "2026-09-09T05:05:00.000Z"


def assistant(blocks, usage=None, timestamp=TS, model="claude-haiku-4-5-20251001"):
    message = {"role": "assistant", "model": model, "content": blocks}
    if usage:
        message["usage"] = usage
    return {"type": "assistant", "timestamp": timestamp, "message": message}


def tool_use(name, **params):
    return {"type": "tool_use", "id": "t1", "name": name, "input": params}


class TestTokens(unittest.TestCase):
    def test_sums_all_four_token_kinds(self):
        d = AgentDigest()
        d.ingest([assistant([], usage={"input_tokens": 10, "output_tokens": 5,
                                       "cache_read_input_tokens": 100,
                                       "cache_creation_input_tokens": 7})])
        self.assertEqual(d.tokens, {"input": 10, "output": 5,
                                    "cache_read": 100, "cache_create": 7})

    def test_accumulates_across_entries_and_ingests(self):
        d = AgentDigest()
        d.ingest([assistant([], usage={"input_tokens": 10, "output_tokens": 5})])
        d.ingest([assistant([], usage={"input_tokens": 1, "output_tokens": 2})])
        self.assertEqual(d.tokens["input"], 11)
        self.assertEqual(d.tokens["output"], 7)

    def test_missing_usage_is_safe(self):
        d = AgentDigest()
        d.ingest([assistant([])])
        self.assertEqual(d.tokens, {})


class TestToolCalls(unittest.TestCase):
    def test_read_and_write_targets_are_file_paths(self):
        d = AgentDigest()
        d.ingest([assistant([tool_use("Write", file_path="src/a.py", content="x"),
                             tool_use("Read", file_path="src/b.py")])])
        self.assertEqual([(t.name, t.target) for t in d.tool_calls],
                         [("Write", "src/a.py"), ("Read", "src/b.py")])

    def test_files_written_and_read_are_separated(self):
        d = AgentDigest()
        d.ingest([assistant([tool_use("Write", file_path="src/a.py"),
                             tool_use("Edit", file_path="src/a.py"),
                             tool_use("Read", file_path="src/b.py")])])
        self.assertEqual(d.files_written, ["src/a.py"])
        self.assertEqual(d.files_read, ["src/b.py"])

    def test_bash_target_is_the_truncated_command(self):
        d = AgentDigest()
        d.ingest([assistant([tool_use("Bash", command="x" * 300)])])
        self.assertTrue(d.tool_calls[0].target.startswith("x"))
        self.assertLessEqual(len(d.tool_calls[0].target), 123)

    def test_grep_records_pattern_and_reads_path(self):
        d = AgentDigest()
        d.ingest([assistant([tool_use("Grep", pattern="def foo", path="src/")])])
        self.assertEqual(d.tool_calls[0].target, "def foo")
        self.assertEqual(d.files_read, ["src/"])

    def test_unknown_tool_does_not_raise(self):
        d = AgentDigest()
        d.ingest([assistant([tool_use("SomeFutureTool", whatever={"a": 1})])])
        self.assertEqual(d.tool_calls[0].name, "SomeFutureTool")


class TestActivityAndFinalText(unittest.TestCase):
    def test_last_activity_tracks_latest_timestamp(self):
        d = AgentDigest()
        d.ingest([assistant([], timestamp=TS), assistant([], timestamp=TS_LATER)])
        d2 = AgentDigest()
        d2.ingest([assistant([], timestamp=TS_LATER)])
        self.assertEqual(d.last_activity_at, d2.last_activity_at)

    def test_final_text_is_the_last_assistant_text_block(self):
        d = AgentDigest()
        d.ingest([assistant([{"type": "text", "text": "first"}]),
                  assistant([{"type": "text", "text": "final answer"}])])
        self.assertEqual(d.final_text, "final answer")

    def test_ends_mid_tool_when_last_entry_is_an_unanswered_tool_use(self):
        d = AgentDigest()
        d.ingest([assistant([tool_use("Bash", command="sleep 100")])])
        self.assertTrue(d.ended_mid_tool)

    def test_not_mid_tool_when_a_result_followed(self):
        d = AgentDigest()
        d.ingest([assistant([tool_use("Bash", command="ls")]),
                  {"type": "user", "timestamp": TS_LATER, "message": {"role": "user",
                   "content": [{"type": "tool_result", "tool_use_id": "t1",
                                "content": "ok"}]}}])
        self.assertFalse(d.ended_mid_tool)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 3: Run the test to verify it fails**

Run: `python -m unittest tests.test_agentlog -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'orchestra.agentlog'`

- [ ] **Step 4: Write `orchestra/agentlog.py`**

```python
"""Digesting a subagent's own transcript into the numbers the dashboard shows."""

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from orchestra.model import ToolCall
from orchestra.parent import content_blocks, parse_timestamp

_MAX_TARGET = 120

# Which input field best describes what a tool call acted on.
_TARGET_FIELDS = {
    "Read": "file_path", "Write": "file_path", "Edit": "file_path",
    "NotebookEdit": "notebook_path", "Glob": "pattern", "Grep": "pattern",
    "Bash": "command", "PowerShell": "command", "Agent": "description",
    "Task": "description", "SendMessage": "to", "Skill": "skill",
    "WebFetch": "url", "WebSearch": "query",
}

_WRITE_TOOLS = {"Write": "file_path", "Edit": "file_path",
                "NotebookEdit": "notebook_path"}
_READ_TOOLS = {"Read": "file_path", "Grep": "path", "Glob": "path"}

_USAGE_FIELDS = (
    ("input_tokens", "input"),
    ("output_tokens", "output"),
    ("cache_read_input_tokens", "cache_read"),
    ("cache_creation_input_tokens", "cache_create"),
)


def _target_for(name: str, params: Dict[str, Any]) -> str:
    field_name = _TARGET_FIELDS.get(name)
    value = params.get(field_name) if field_name else None
    if value is None:
        for candidate in params.values():
            if isinstance(candidate, str) and candidate:
                value = candidate
                break
    text = str(value) if value is not None else ""
    return text[:_MAX_TARGET] + "..." if len(text) > _MAX_TARGET else text


@dataclass
class AgentDigest:
    """Accumulates across incremental reads; safe to ingest repeatedly."""
    tokens: Dict[str, int] = field(default_factory=dict)
    tool_calls: List[ToolCall] = field(default_factory=list)
    files_written: List[str] = field(default_factory=list)
    files_read: List[str] = field(default_factory=list)
    last_activity_at: Optional[float] = None
    final_text: str = ""
    model: str = ""
    ended_mid_tool: bool = False
    _open_tool_ids: set = field(default_factory=set)

    def ingest(self, entries: List[Dict[str, Any]]) -> None:
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            at = parse_timestamp(entry.get("timestamp"))
            if at and (self.last_activity_at is None or at > self.last_activity_at):
                self.last_activity_at = at
            message = entry.get("message")
            if isinstance(message, dict):
                if isinstance(message.get("model"), str):
                    self.model = message["model"]
                self._add_usage(message.get("usage"))
            self._add_blocks(entry, at)
        self.ended_mid_tool = bool(self._open_tool_ids)

    def _add_usage(self, usage: Any) -> None:
        if not isinstance(usage, dict):
            return
        for source, label in _USAGE_FIELDS:
            value = usage.get(source)
            if isinstance(value, int) and value:
                self.tokens[label] = self.tokens.get(label, 0) + value

    def _add_blocks(self, entry: Dict[str, Any], at: Optional[float]) -> None:
        for block in content_blocks(entry):
            kind = block.get("type")
            if kind == "text":
                text = str(block.get("text", "")).strip()
                if text:
                    self.final_text = text
            elif kind == "tool_use":
                name = str(block.get("name", ""))
                params = block.get("input") if isinstance(block.get("input"), dict) else {}
                self.tool_calls.append(
                    ToolCall(name=name, target=_target_for(name, params), timestamp=at))
                self._open_tool_ids.add(str(block.get("id", "")))
                self._record_files(name, params)
            elif kind == "tool_result":
                self._open_tool_ids.discard(str(block.get("tool_use_id", "")))

    def _record_files(self, name: str, params: Dict[str, Any]) -> None:
        write_field = _WRITE_TOOLS.get(name)
        if write_field:
            path = params.get(write_field)
            if isinstance(path, str) and path and path not in self.files_written:
                self.files_written.append(path)
        read_field = _READ_TOOLS.get(name)
        if read_field:
            path = params.get(read_field)
            if isinstance(path, str) and path and path not in self.files_read:
                self.files_read.append(path)
```

- [ ] **Step 5: Run the test to verify it passes**

Run: `python -m unittest tests.test_agentlog -v`
Expected: PASS, 12 tests.

- [ ] **Step 6: Commit**

```bash
git add orchestra/agentlog.py orchestra/parent.py tests/test_agentlog.py
git commit -m "feat: digest subagent transcripts into tokens, tool calls, and file sets"
```

---

## Task 8: Rounds and the status state machine

**Files:**
- Create: `orchestra/status.py`
- Test: `tests/test_status.py`

**Interfaces:**
- Consumes: `Round` from `orchestra.model`; `LaunchRecord`, `ResultRecord`, `Notification` from `orchestra.parent`; `AgentDigest` from `orchestra.agentlog`.
- Produces: `build_rounds(launch, result, notifications, digest) -> List[Round]`; `compute_status(rounds, digest, now, session_live) -> str`.

**Spec ambiguity resolved here.** Spec §8 lets two rules match the same agent: an agent whose transcript ends mid-tool-call is `failed`, while an agent with no terminal record and a dead session is `orphaned`. Precedence, decided here and documented in the code:

1. A closed final round → that round's own status (`completed` / `failed`).
2. Open round, transcript ends mid-tool-call, session not live → `failed`. We can see it died holding a tool; that is stronger evidence than mere absence.
3. Open round, session not live → `orphaned`.
4. Open round, session live, silent longer than `STALL_THRESHOLD_S` → `stalled`.
5. Otherwise → `running`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_status.py`:

```python
import unittest

from orchestra import constants as C
from orchestra.agentlog import AgentDigest
from orchestra.parent import LaunchRecord, Notification, ResultRecord
from orchestra.status import build_rounds, compute_status

T0, T1, T2, T3 = 1000.0, 1100.0, 1200.0, 1300.0


def launch(at=T0):
    return LaunchRecord(tool_use_id="toolu_1", launched_at=at)


def note(at, status="completed", result="done"):
    return Notification(agent_id="a1", tool_use_id="toolu_1", status=status,
                        result=result, at=at)


def digest(last=None, mid_tool=False):
    d = AgentDigest()
    d.last_activity_at = last
    d.ended_mid_tool = mid_tool
    return d


class TestBuildRounds(unittest.TestCase):
    def test_background_agent_with_one_notification(self):
        rounds = build_rounds(launch(), None, [note(T1)], digest(last=T1))
        self.assertEqual(len(rounds), 1)
        self.assertEqual(rounds[0].started_at, T0)
        self.assertEqual(rounds[0].ended_at, T1)
        self.assertEqual(rounds[0].status, C.COMPLETED)
        self.assertEqual(rounds[0].result, "done")

    def test_resumed_agent_produces_two_closed_rounds(self):
        rounds = build_rounds(launch(), None,
                              [note(T1, result="first"), note(T3, result="second")],
                              digest(last=T3))
        self.assertEqual(len(rounds), 2)
        self.assertEqual((rounds[0].started_at, rounds[0].ended_at), (T0, T1))
        self.assertEqual((rounds[1].started_at, rounds[1].ended_at), (T1, T3))
        self.assertEqual(rounds[1].result, "second")

    def test_activity_after_last_notification_opens_a_new_round(self):
        rounds = build_rounds(launch(), None, [note(T1)], digest(last=T2))
        self.assertEqual(len(rounds), 2)
        self.assertIsNone(rounds[1].ended_at)
        self.assertEqual(rounds[1].started_at, T1)

    def test_failed_notification_status(self):
        rounds = build_rounds(launch(), None, [note(T1, status="error")], digest(last=T1))
        self.assertEqual(rounds[0].status, C.FAILED)

    def test_inline_agent_closes_on_its_tool_result(self):
        result = ResultRecord(tool_use_id="toolu_1", launch_mode="inline",
                              inline_result="the answer", at=T1)
        rounds = build_rounds(launch(), result, [], digest(last=T1))
        self.assertEqual(len(rounds), 1)
        self.assertEqual(rounds[0].ended_at, T1)
        self.assertEqual(rounds[0].result, "the answer")
        self.assertEqual(rounds[0].status, C.COMPLETED)

    def test_inline_error_result_is_failed(self):
        result = ResultRecord(tool_use_id="toolu_1", launch_mode="inline",
                              is_error=True, inline_result="boom", at=T1)
        rounds = build_rounds(launch(), result, [], digest(last=T1))
        self.assertEqual(rounds[0].status, C.FAILED)

    def test_no_terminal_record_leaves_one_open_round(self):
        rounds = build_rounds(launch(), None, [], digest(last=T1))
        self.assertEqual(len(rounds), 1)
        self.assertIsNone(rounds[0].ended_at)


class TestComputeStatus(unittest.TestCase):
    def test_closed_round_wins(self):
        rounds = build_rounds(launch(), None, [note(T1)], digest(last=T1))
        self.assertEqual(compute_status(rounds, digest(last=T1), now=99999,
                                        session_live=False), C.COMPLETED)

    def test_running_when_recently_active(self):
        rounds = build_rounds(launch(), None, [], digest(last=T1))
        status = compute_status(rounds, digest(last=T1), now=T1 + 10, session_live=True)
        self.assertEqual(status, C.RUNNING)

    def test_stalled_when_silent_past_threshold_but_session_live(self):
        rounds = build_rounds(launch(), None, [], digest(last=T1))
        status = compute_status(rounds, digest(last=T1),
                                now=T1 + C.STALL_THRESHOLD_S + 1, session_live=True)
        self.assertEqual(status, C.STALLED)

    def test_orphaned_when_session_is_dead(self):
        rounds = build_rounds(launch(), None, [], digest(last=T1))
        status = compute_status(rounds, digest(last=T1), now=T1 + 9999,
                                session_live=False)
        self.assertEqual(status, C.ORPHANED)

    def test_mid_tool_death_beats_orphaned(self):
        d = digest(last=T1, mid_tool=True)
        rounds = build_rounds(launch(), None, [], d)
        self.assertEqual(compute_status(rounds, d, now=T1 + 9999, session_live=False),
                         C.FAILED)

    def test_mid_tool_while_session_live_is_still_running(self):
        d = digest(last=T1, mid_tool=True)
        rounds = build_rounds(launch(), None, [], d)
        self.assertEqual(compute_status(rounds, d, now=T1 + 5, session_live=True),
                         C.RUNNING)

    def test_resumed_agent_mid_second_round_is_running(self):
        d = digest(last=T2)
        rounds = build_rounds(launch(), None, [note(T1)], d)
        self.assertEqual(compute_status(rounds, d, now=T2 + 5, session_live=True),
                         C.RUNNING)

    def test_no_rounds_is_unknown(self):
        self.assertEqual(compute_status([], digest(), now=T1, session_live=True),
                         C.UNKNOWN)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `python -m unittest tests.test_status -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'orchestra.status'`

- [ ] **Step 3: Write `orchestra/status.py`**

```python
"""Reconstructing an agent's lifecycle from the records it left behind.

A task-notification can fire more than once for the same agent, because a
finished agent can be resumed with SendMessage. An agent therefore has a list
of rounds, not a single start and end.
"""

from typing import List, Optional

from orchestra import constants as C
from orchestra.agentlog import AgentDigest
from orchestra.model import Round
from orchestra.parent import LaunchRecord, Notification, ResultRecord


def _notification_status(raw: str) -> str:
    return C.COMPLETED if (raw or "").strip().lower() == "completed" else C.FAILED


def build_rounds(launch: Optional[LaunchRecord],
                 result: Optional[ResultRecord],
                 notifications: List[Notification],
                 digest: AgentDigest) -> List[Round]:
    """One Round per observed start-to-finish pass, plus an open one if still going."""
    started = launch.launched_at if launch else None
    rounds: List[Round] = []

    if notifications:
        cursor = started
        for note in notifications:
            rounds.append(Round(started_at=cursor, ended_at=note.at,
                                status=_notification_status(note.status),
                                result=note.result))
            cursor = note.at
        last_end = rounds[-1].ended_at
        activity = digest.last_activity_at
        # Activity after the final notification means the agent was resumed.
        if activity is not None and last_end is not None and activity > last_end:
            rounds.append(Round(started_at=last_end, ended_at=None, status=C.RUNNING))
        return rounds

    if result is not None and result.launch_mode == "inline" and result.at is not None:
        return [Round(started_at=started, ended_at=result.at,
                      status=C.FAILED if result.is_error else C.COMPLETED,
                      result=result.inline_result)]

    if started is None and digest.last_activity_at is None:
        return []
    return [Round(started_at=started, ended_at=None, status=C.RUNNING)]


def compute_status(rounds: List[Round], digest: AgentDigest, now: float,
                   session_live: bool) -> str:
    """Precedence is deliberate: see the plan's Task 8 notes."""
    if not rounds:
        return C.UNKNOWN

    final = rounds[-1]
    if final.ended_at is not None:
        return final.status

    if not session_live:
        # Dying while holding an open tool call is visible evidence of failure;
        # plain absence only tells us the session went away.
        return C.FAILED if digest.ended_mid_tool else C.ORPHANED

    last_seen = digest.last_activity_at or final.started_at
    if last_seen is not None and (now - last_seen) > C.STALL_THRESHOLD_S:
        return C.STALLED
    return C.RUNNING
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `python -m unittest tests.test_status -v`
Expected: PASS, 15 tests.

- [ ] **Step 5: Commit**

```bash
git add orchestra/status.py tests/test_status.py
git commit -m "feat: reconstruct agent rounds and status from transcript records"
```

---

## Task 9: The four edge inferencers

**Files:**
- Create: `orchestra/edges.py`
- Test: `tests/test_edges.py`

**Interfaces:**
- Consumes: `Agent`, `Edge`, `HubFile`, `ToolCall` from `orchestra.model`.
- Produces: `infer_edges(agents: List[Agent]) -> Tuple[List[Edge], List[HubFile]]`; `normalize_path(path: str) -> str`.

**Scope decision:** artifact edges are built only from path-carrying tools — `Write`/`Edit`/`NotebookEdit` for writes, `Read` for reads. `Grep` and `Glob` still populate `files_read` in `agentlog.py` for display, but a directory-wide grep is not evidence that an agent consumed a specific file, and treating it as such would flood the graph. This is why `edges.py` declares its own narrower `WRITE_TOOL_NAMES` / `READ_TOOL_NAMES` tuples rather than importing `agentlog`'s tables — the two modules genuinely want different sets, and sharing one would silently widen the graph.

- [ ] **Step 1: Write the failing test**

Create `tests/test_edges.py`:

```python
import unittest

from orchestra import constants as C
from orchestra.edges import infer_edges, normalize_path
from orchestra.model import Agent, Round, ToolCall


def agent(agent_id, start, end, parent=None, writes=(), reads=(),
          result="", brief="", sends=()):
    calls = []
    for path, at in writes:
        calls.append(ToolCall(name="Write", target=path, timestamp=at))
    for path, at in reads:
        calls.append(ToolCall(name="Read", target=path, timestamp=at))
    for target, at in sends:
        calls.append(ToolCall(name="SendMessage", target=target, timestamp=at))
    return Agent(agent_id=agent_id, tool_use_id="toolu_" + agent_id,
                 parent_agent_id=parent, brief=brief, result=result,
                 tool_calls=calls, status=C.COMPLETED,
                 rounds=[Round(started_at=start, ended_at=end)])


class TestNormalizePath(unittest.TestCase):
    def test_worktree_path_collapses_onto_the_main_path(self):
        a = normalize_path(r"E:\proj\.claude\worktrees\feature-x\src\main.py")
        b = normalize_path(r"E:\proj\src\main.py")
        self.assertEqual(a, b)
        self.assertNotIn("worktrees", a)

    def test_separators_and_case_are_normalized(self):
        self.assertEqual(normalize_path(r"SRC\Main.py"), normalize_path("src/main.py"))


class TestSpawnEdges(unittest.TestCase):
    def test_orchestrator_is_the_default_parent(self):
        edges, _ = infer_edges([agent("a1", 0, 10)])
        spawn = [e for e in edges if e.kind == "spawn"]
        self.assertEqual(len(spawn), 1)
        self.assertEqual(spawn[0].src, C.ORCHESTRATOR_ID)
        self.assertEqual(spawn[0].dst, "a1")
        self.assertEqual(spawn[0].confidence, "exact")

    def test_nested_agent_points_at_its_launcher(self):
        edges, _ = infer_edges([agent("a1", 0, 100), agent("a2", 10, 50, parent="a1")])
        spawn = {(e.src, e.dst) for e in edges if e.kind == "spawn"}
        self.assertIn(("a1", "a2"), spawn)


class TestArtifactEdges(unittest.TestCase):
    def test_write_then_read_creates_an_edge_with_evidence(self):
        a = agent("a1", 0, 10, writes=[("src/main.py", 5)])
        b = agent("a2", 20, 30, reads=[("src/main.py", 25)])
        edges, _ = infer_edges([a, b])
        art = [e for e in edges if e.kind == "artifact"]
        self.assertEqual(len(art), 1)
        self.assertEqual((art[0].src, art[0].dst), ("a1", "a2"))
        self.assertEqual(art[0].confidence, "exact")
        self.assertIn("main.py", art[0].evidence["path"])

    def test_read_before_write_creates_no_edge(self):
        a = agent("a1", 20, 30, writes=[("src/main.py", 25)])
        b = agent("a2", 0, 10, reads=[("src/main.py", 5)])
        edges, _ = infer_edges([a, b])
        self.assertEqual([e for e in edges if e.kind == "artifact"], [])

    def test_self_read_creates_no_edge(self):
        a = agent("a1", 0, 10, writes=[("src/main.py", 2)], reads=[("src/main.py", 5)])
        edges, _ = infer_edges([a])
        self.assertEqual([e for e in edges if e.kind == "artifact"], [])

    def test_worktree_paths_still_match(self):
        a = agent("a1", 0, 10, writes=[(r"E:\p\.claude\worktrees\wt\src\a.py", 5)])
        b = agent("a2", 20, 30, reads=[(r"E:\p\src\a.py", 25)])
        edges, _ = infer_edges([a, b])
        self.assertEqual(len([e for e in edges if e.kind == "artifact"]), 1)

    def test_hub_file_is_collapsed_not_edged(self):
        writer = agent("w", 0, 5, writes=[("other.py", 1)])
        readers = [agent("r{}".format(i), 10 + i, 20 + i, reads=[("PLAN.md", 11 + i)])
                   for i in range(C.HUB_FILE_THRESHOLD + 1)]
        edges, hubs = infer_edges([writer] + readers)
        self.assertEqual([e for e in edges if e.kind == "artifact"], [])
        self.assertEqual(len(hubs), 1)
        self.assertIn("plan.md", hubs[0].path)
        self.assertEqual(len(hubs[0].reader_ids), C.HUB_FILE_THRESHOLD + 1)

    def test_a_file_written_during_the_run_is_never_a_hub(self):
        writer = agent("w", 0, 5, writes=[("PLAN.md", 1)])
        readers = [agent("r{}".format(i), 10 + i, 20 + i, reads=[("PLAN.md", 11 + i)])
                   for i in range(C.HUB_FILE_THRESHOLD + 1)]
        edges, hubs = infer_edges([writer] + readers)
        self.assertEqual(hubs, [])
        self.assertEqual(len([e for e in edges if e.kind == "artifact"]),
                         C.HUB_FILE_THRESHOLD + 1)


class TestMessageEdges(unittest.TestCase):
    def test_sendmessage_to_a_known_agent(self):
        a = agent("a1", 0, 10, sends=[("a2", 8)])
        b = agent("a2", 20, 30)
        edges, _ = infer_edges([a, b])
        msg = [e for e in edges if e.kind == "message"]
        self.assertEqual((msg[0].src, msg[0].dst), ("a1", "a2"))

    def test_sendmessage_to_an_unknown_target_is_dropped(self):
        a = agent("a1", 0, 10, sends=[("somebody-else", 8)])
        edges, _ = infer_edges([a])
        self.assertEqual([e for e in edges if e.kind == "message"], [])


SHARED = ("the parser must normalize windows paths before comparing them "
          "because otherwise every artifact edge silently fails to match and "
          "the dependency graph comes out completely empty on windows machines ")


class TestHandoffEdges(unittest.TestCase):
    def test_quoted_result_creates_an_inferred_edge_with_evidence(self):
        a = agent("a1", 0, 10, result="Findings: " + SHARED)
        b = agent("a2", 20, 30, brief="Fix this finding: " + SHARED + " Go.")
        edges, _ = infer_edges([a, b])
        hand = [e for e in edges if e.kind == "handoff"]
        self.assertEqual(len(hand), 1)
        self.assertEqual(hand[0].confidence, "inferred")
        self.assertGreater(hand[0].evidence["score"], 0)
        self.assertIn("normalize windows paths", hand[0].evidence["snippet"])

    def test_no_edge_backwards_in_time(self):
        a = agent("a1", 40, 50, result="Findings: " + SHARED)
        b = agent("a2", 0, 10, brief="Fix this finding: " + SHARED)
        edges, _ = infer_edges([a, b])
        self.assertEqual([e for e in edges if e.kind == "handoff"], [])

    def test_unrelated_text_creates_no_edge(self):
        a = agent("a1", 0, 10, result="Everything passed, nothing to report.")
        b = agent("a2", 20, 30, brief="Write a haiku about the ocean.")
        edges, _ = infer_edges([a, b])
        self.assertEqual([e for e in edges if e.kind == "handoff"], [])

    def test_handoff_folds_into_an_existing_exact_edge(self):
        a = agent("a1", 0, 10, writes=[("out.md", 5)], result="Findings: " + SHARED)
        b = agent("a2", 20, 30, reads=[("out.md", 25)],
                  brief="Fix this finding: " + SHARED)
        edges, _ = infer_edges([a, b])
        pair = [e for e in edges if (e.src, e.dst) == ("a1", "a2")
                and e.kind in ("artifact", "handoff")]
        self.assertEqual(len(pair), 1)
        self.assertEqual(pair[0].kind, "artifact")
        self.assertIn("handoff", pair[0].evidence)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `python -m unittest tests.test_edges -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'orchestra.edges'`

- [ ] **Step 3: Write `orchestra/edges.py`**

```python
"""Inferring how agents were connected.

Three of the four edge kinds are exact. The fourth, handoff, is a guess, and it
is labelled as one and ships the evidence that produced it so a reader can
dismiss it.
"""

import difflib
import os
import re
from typing import Dict, List, Optional, Set, Tuple

from orchestra import constants as C
from orchestra.model import Agent, Edge, HubFile

# Captures the project root so a worktree path collapses onto the main path:
# E:\p\.claude\worktrees\wt\src\a.py  ->  E:\p\src\a.py
# Dropping the root instead would leave worktree paths relative and plain paths
# absolute, and the two would never compare equal.
_WORKTREE = re.compile(r"(^.*?)[\\/]\.claude[\\/]worktrees[\\/][^\\/]+[\\/]",
                       re.IGNORECASE)
_WORD = re.compile(r"[a-z0-9]+")

WRITE_TOOL_NAMES = ("Write", "Edit", "NotebookEdit")
READ_TOOL_NAMES = ("Read",)


def normalize_path(path: str) -> str:
    """Make paths from different agents and worktrees comparable."""
    if not path:
        return ""
    stripped = _WORKTREE.sub(r"\1/", path)
    stripped = stripped.replace("\\", "/")
    stripped = os.path.normpath(stripped).replace("\\", "/")
    return stripped.lower().lstrip("./")


def _paths(agent: Agent, names: Tuple[str, ...]) -> List[Tuple[str, Optional[float]]]:
    out = []
    for call in agent.tool_calls:
        if call.name in names and call.target:
            out.append((normalize_path(call.target), call.timestamp))
    return out


def _spawn_edges(agents: List[Agent]) -> List[Edge]:
    return [Edge(src=a.parent_agent_id or C.ORCHESTRATOR_ID, dst=a.agent_id,
                 kind="spawn", confidence="exact",
                 evidence={"tool_use_id": a.tool_use_id})
            for a in agents]


def _artifact_edges(agents: List[Agent]) -> Tuple[List[Edge], List[HubFile]]:
    writes: Dict[str, List[Tuple[str, Optional[float]]]] = {}
    reads: Dict[str, List[Tuple[str, Optional[float]]]] = {}
    for agent in agents:
        for path, at in _paths(agent, WRITE_TOOL_NAMES):
            writes.setdefault(path, []).append((agent.agent_id, at))
        for path, at in _paths(agent, READ_TOOL_NAMES):
            reads.setdefault(path, []).append((agent.agent_id, at))

    edges: List[Edge] = []
    hubs: List[HubFile] = []
    for path, readers in reads.items():
        writers = writes.get(path, [])
        if not writers:
            # Read widely, written by nobody: shared context, not a dependency.
            if len(readers) > C.HUB_FILE_THRESHOLD:
                hubs.append(HubFile(path=path,
                                    reader_ids=[r for r, _ in readers]))
            continue
        for reader_id, read_at in readers:
            for writer_id, write_at in writers:
                if writer_id == reader_id:
                    continue
                if read_at is not None and write_at is not None and read_at < write_at:
                    continue
                edges.append(Edge(src=writer_id, dst=reader_id, kind="artifact",
                                  confidence="exact",
                                  evidence={"path": path, "written_at": write_at,
                                            "read_at": read_at}))
    return edges, hubs


def _message_edges(agents: List[Agent]) -> List[Edge]:
    known = {a.agent_id for a in agents}
    edges = []
    for agent in agents:
        for call in agent.tool_calls:
            if call.name == "SendMessage" and call.target in known:
                edges.append(Edge(src=agent.agent_id, dst=call.target, kind="message",
                                  confidence="exact",
                                  evidence={"at": call.timestamp}))
    return edges


def _shingles(words: List[str], size: int) -> Set[Tuple[str, ...]]:
    return {tuple(words[i:i + size]) for i in range(max(0, len(words) - size + 1))}


def _longest_run(src_words: List[str], dst_words: List[str]) -> Tuple[int, str]:
    matcher = difflib.SequenceMatcher(None, src_words, dst_words, autojunk=False)
    match = matcher.find_longest_match(0, len(src_words), 0, len(dst_words))
    snippet = " ".join(src_words[match.a:match.a + match.size])
    return match.size, snippet


def _handoff_edges(agents: List[Agent]) -> List[Edge]:
    edges = []
    for src in agents:
        if not src.result or src.ended_at is None:
            continue
        src_words = _WORD.findall(src.result.lower())
        src_shingles = _shingles(src_words, C.SHINGLE_SIZE)
        if not src_shingles:
            continue
        for dst in agents:
            if dst.agent_id == src.agent_id or not dst.brief:
                continue
            if dst.started_at is None or dst.started_at < src.ended_at:
                continue
            dst_words = _WORD.findall(dst.brief.lower())
            overlap = src_shingles & _shingles(dst_words, C.SHINGLE_SIZE)
            score = len(overlap) / float(len(src_shingles))
            run, snippet = _longest_run(src_words, dst_words)
            if score < C.HANDOFF_CONTAINMENT and run < C.HANDOFF_RUN_WORDS:
                continue
            edges.append(Edge(src=src.agent_id, dst=dst.agent_id, kind="handoff",
                              confidence="inferred",
                              evidence={"score": round(score, 3),
                                        "run_words": run,
                                        "snippet": snippet[:400]}))
    return edges


def infer_edges(agents: List[Agent]) -> Tuple[List[Edge], List[HubFile]]:
    """All four kinds, deduplicated. Handoff folds into an exact edge if one exists."""
    edges = _spawn_edges(agents)
    artifact, hubs = _artifact_edges(agents)
    edges.extend(artifact)
    edges.extend(_message_edges(agents))

    seen: Dict[Tuple[str, str, str], Edge] = {}
    deduped: List[Edge] = []
    for edge in edges:
        key = (edge.src, edge.dst, edge.kind)
        if key in seen:
            continue
        seen[key] = edge
        deduped.append(edge)

    exact_pairs = {(e.src, e.dst): e for e in deduped
                   if e.kind in ("artifact", "message")}
    for edge in _handoff_edges(agents):
        existing = exact_pairs.get((edge.src, edge.dst))
        if existing is not None:
            existing.evidence["handoff"] = edge.evidence
            continue
        key = (edge.src, edge.dst, edge.kind)
        if key not in seen:
            seen[key] = edge
            deduped.append(edge)
    return deduped, hubs
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `python -m unittest tests.test_edges -v`
Expected: PASS, 16 tests.

- [ ] **Step 5: Commit**

```bash
git add orchestra/edges.py tests/test_edges.py
git commit -m "feat: infer spawn, artifact, message, and handoff edges with evidence"
```

---

## Task 10: Assembling the Run, end to end

**Files:**
- Create: `orchestra/build.py`
- Create: `tests/fixtures.py` (fixture builder, shared by later tasks)
- Modify: `orchestra/model.py` — route every serialized string through `redact.scrub`
- Test: `tests/test_build.py`

**Interfaces:**
- Consumes: everything from Tasks 1–9.
- Produces: `RunBuilder(paths: SessionPaths, now_fn=time.time)` with `.refresh() -> Run` and a `.paths` attribute (Task 11's service reads `builder.paths.project_dir`); `tests/fixtures.py` exposing `build_session(root, session_id="s1") -> SessionPaths` and `build_nested_session(root, session_id="n1") -> SessionPaths`.

This is the task that makes Orchestra real: after it, `python -c "..."` can print a complete `Run` for a live session with no server and no UI.

- [ ] **Step 1: Wire redaction into `orchestra/model.py`**

Add the import at the top:

```python
from orchestra.redact import scrub, scrub_obj
```

Then change the three serializers so every string leaving the model is scrubbed. In `to_light_dict`, wrap the two free-text fields:

```python
            "description": scrub(self.description),
            ...
            "objective": scrub(self.objective.text),
```

In `to_detail_dict`, wrap the rest:

```python
        d.update({
            "brief": scrub(self.brief),
            "result": scrub(self.result),
            "objective_source": self.objective.source,
            "expected_output": scrub(self.expected_output.text),
            "expected_output_source": self.expected_output.source,
            "tool_calls": [{"name": t.name, "target": scrub(t.target),
                            "timestamp": t.timestamp} for t in self.tool_calls],
            "files_written": list(self.files_written),
            "files_read": list(self.files_read),
            "transcript_path": self.transcript_path,
        })
```

And in `Edge.to_dict`, scrub the evidence, which carries handoff snippets:

```python
        return {"src": self.src, "dst": self.dst, "kind": self.kind,
                "confidence": self.confidence, "evidence": scrub_obj(self.evidence)}
```

- [ ] **Step 2: Add the redaction test to `tests/test_model.py`**

Append this class:

```python
class TestSerializationRedacts(unittest.TestCase):
    def test_brief_and_result_are_scrubbed(self):
        token = "<fake-github-token>"
        a = make_agent(brief="use " + token, result="also " + token)
        d = a.to_detail_dict()
        self.assertNotIn("ABCDEFGHIJ", d["brief"])
        self.assertNotIn("ABCDEFGHIJ", d["result"])

    def test_edge_evidence_is_scrubbed(self):
        e = Edge(src="a1", dst="a2", kind="handoff", confidence="inferred",
                 evidence={"snippet": "token <fake-github-token>"})
        self.assertNotIn("ABCDEFGHIJ", e.to_dict()["evidence"]["snippet"])
```

Run: `python -m unittest tests.test_model -v` — Expected: PASS, 8 tests.

- [ ] **Step 3: Write the fixture builder**

Create `tests/fixtures.py`:

```python
"""Builds a synthetic ~/.claude tree. No test ever touches the real one."""

import json
import os
from typing import Any, Dict, List, Optional

from orchestra.locate import SessionPaths

T0 = "2026-09-09T05:00:00.000Z"


def ts(offset_s: int) -> str:
    """A timestamp offset_s seconds after T0."""
    minutes, seconds = divmod(offset_s, 60)
    hours, minutes = divmod(minutes, 60)
    return "2026-09-09T{:02d}:{:02d}:{:02d}.000Z".format(5 + hours, minutes, seconds)


def launch(tool_use_id: str, description: str, prompt: str, at: int,
           turn: str = "turn-1", model: str = "haiku") -> Dict[str, Any]:
    return {"uuid": turn, "timestamp": ts(at), "type": "assistant",
            "cwd": r"E:\proj",
            "message": {"role": "assistant", "content": [
                {"type": "tool_use", "id": tool_use_id, "name": "Agent",
                 "input": {"description": description, "prompt": prompt,
                           "model": model}}]}}


def background_result(tool_use_id: str, agent_id: str, at: int) -> Dict[str, Any]:
    text = ("Async agent launched successfully.\n"
            "agentId: {} (internal ID)\n".format(agent_id))
    return {"uuid": "r-" + agent_id, "timestamp": ts(at), "type": "user",
            "message": {"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": tool_use_id,
                 "content": [{"type": "text", "text": text}]}]}}


def notification(agent_id: str, tool_use_id: str, result: str, at: int,
                 status: str = "completed") -> Dict[str, Any]:
    body = ("<task-notification>\n<task-id>{}</task-id>\n"
            "<tool-use-id>{}</tool-use-id>\n<status>{}</status>\n"
            "<summary>done</summary>\n<result>{}</result>\n"
            "</task-notification>").format(agent_id, tool_use_id, status, result)
    return {"uuid": "n-" + agent_id + str(at), "timestamp": ts(at), "type": "user",
            "message": {"role": "user", "content": body}}


def agent_entry(blocks: List[Dict[str, Any]], at: int,
                usage: Optional[Dict[str, int]] = None) -> Dict[str, Any]:
    message: Dict[str, Any] = {"role": "assistant", "content": blocks,
                               "model": "claude-haiku-4-5-20251001"}
    if usage:
        message["usage"] = usage
    return {"isSidechain": True, "timestamp": ts(at), "type": "assistant",
            "message": message}


def write_jsonl(path: str, entries: List[Dict[str, Any]]) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        for entry in entries:
            fh.write(json.dumps(entry) + "\n")


def build_session(root: str, session_id: str = "s1") -> SessionPaths:
    """A three-agent run: planner writes a plan, two implementers read it.

    a1  plan      0s -> 60s   completed, wrote PLAN.md
    a2  impl one  70s -> 140s completed, read PLAN.md, quotes a1's result
    a3  impl two  70s -> ...  still running
    """
    project = os.path.join(root, "projects", "E--proj")
    session_jsonl = os.path.join(project, session_id + ".jsonl")
    subagents = os.path.join(project, session_id, "subagents")

    a1_result = ("Wrote the plan to PLAN.md. The parser must normalize windows "
                 "paths before comparing them or every artifact edge fails.")

    parent = [
        launch("toolu_1", "Plan the work",
               "You are planning.\n\n## Deliverable\n\nA written plan at PLAN.md.\n", 0),
        background_result("toolu_1", "a1", 1),
        notification("a1", "toolu_1", a1_result, 60),
        launch("toolu_2", "Implement part one",
               "Follow the plan.\n\n" + a1_result + "\n\n## Deliverable\n\nA commit.\n",
               70, turn="turn-2"),
        background_result("toolu_2", "a2", 71),
        launch("toolu_3", "Implement part two",
               "Follow the plan.\n\n## Deliverable\n\nAnother commit.\n",
               70, turn="turn-2"),
        background_result("toolu_3", "a3", 71),
        notification("a2", "toolu_2", "Implemented part one.", 140),
    ]
    write_jsonl(session_jsonl, parent)

    def meta(path: str, description: str) -> None:
        with open(path, "w", encoding="utf-8") as fh:
            json.dump({"agentType": "general-purpose", "description": description,
                       "toolUseId": path_to_tool[path], "spawnDepth": 1,
                       "requestShape": "background", "model": "haiku"}, fh)

    path_to_tool = {}
    for agent_id, tool_use_id, description in (("a1", "toolu_1", "Plan the work"),
                                               ("a2", "toolu_2", "Implement part one"),
                                               ("a3", "toolu_3", "Implement part two")):
        os.makedirs(subagents, exist_ok=True)
        meta_path = os.path.join(subagents, "agent-{}.meta.json".format(agent_id))
        path_to_tool[meta_path] = tool_use_id
        meta(meta_path, description)

    write_jsonl(os.path.join(subagents, "agent-a1.jsonl"), [
        agent_entry([{"type": "tool_use", "id": "w1", "name": "Write",
                      "input": {"file_path": r"E:\proj\PLAN.md", "content": "plan"}}], 30,
                    usage={"input_tokens": 100, "output_tokens": 50}),
        agent_entry([{"type": "text", "text": a1_result}], 59),
    ])
    write_jsonl(os.path.join(subagents, "agent-a2.jsonl"), [
        agent_entry([{"type": "tool_use", "id": "r1", "name": "Read",
                      "input": {"file_path": r"E:\proj\PLAN.md"}}], 80,
                    usage={"input_tokens": 200, "output_tokens": 20}),
        agent_entry([{"type": "text", "text": "Implemented part one."}], 139),
    ])
    # a3's Read must be answered by a tool_result. An unanswered tool_use sets
    # AgentDigest.ended_mid_tool, and a mid-tool death outranks orphaned in the
    # status precedence — so without this the "orphaned when the session dies"
    # test would get `failed` instead.
    write_jsonl(os.path.join(subagents, "agent-a3.jsonl"), [
        agent_entry([{"type": "tool_use", "id": "r2", "name": "Read",
                      "input": {"file_path": r"E:\proj\PLAN.md"}}], 80,
                    usage={"input_tokens": 210}),
        {"isSidechain": True, "type": "user", "timestamp": ts(81),
         "message": {"role": "user", "content": [
             {"type": "tool_result", "tool_use_id": "r2", "content": "plan"}]}},
    ])

    return SessionPaths(session_id=session_id, session_jsonl=session_jsonl,
                        subagents_dir=subagents, project_dir=project)


def build_nested_session(root: str, session_id: str = "n1") -> SessionPaths:
    """A depth-2 run: the orchestrator spawns a1, and a1 spawns a1b itself.

    spawnDepth > 1 does not appear in any real transcript yet (spec section 20),
    so this synthetic fixture is the only coverage nesting has. Keep it.
    """
    project = os.path.join(root, "projects", "E--proj")
    session_jsonl = os.path.join(project, session_id + ".jsonl")
    subagents = os.path.join(project, session_id, "subagents")

    write_jsonl(session_jsonl, [
        launch("toolu_1", "Lead the work", "You are the lead.\n", 0),
        background_result("toolu_1", "a1", 1),
        notification("a1", "toolu_1", "Lead work finished.", 120),
    ])

    os.makedirs(subagents, exist_ok=True)
    for agent_id, tool_use_id, depth, description in (
            ("a1", "toolu_1", 1, "Lead the work"),
            ("a1b", "toolu_2", 2, "Sub-task of the lead")):
        with open(os.path.join(subagents, "agent-{}.meta.json".format(agent_id)),
                  "w", encoding="utf-8") as fh:
            json.dump({"agentType": "general-purpose", "description": description,
                       "toolUseId": tool_use_id, "spawnDepth": depth,
                       "requestShape": "background", "model": "haiku"}, fh)

    # The nested launch lives in a1's OWN transcript, marked isSidechain with
    # a1's agentId — that is what makes a1 the parent rather than the session.
    nested_launch = {
        "isSidechain": True, "agentId": "a1", "uuid": "turn-inner",
        "timestamp": ts(20), "type": "assistant",
        "message": {"role": "assistant", "content": [
            {"type": "tool_use", "id": "toolu_2", "name": "Agent",
             "input": {"description": "Sub-task of the lead",
                       "prompt": "Do the inner part.\n", "model": "haiku"}}]}}
    nested_result = {
        "isSidechain": True, "agentId": "a1", "uuid": "r-inner",
        "timestamp": ts(21), "type": "user",
        "message": {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": "toolu_2", "content": [
                {"type": "text", "text": "Async agent launched successfully.\n"
                                         "agentId: a1b (internal ID)\n"}]}]}}

    write_jsonl(os.path.join(subagents, "agent-a1.jsonl"), [
        nested_launch, nested_result,
        agent_entry([{"type": "text", "text": "Lead work finished."}], 119),
    ])
    write_jsonl(os.path.join(subagents, "agent-a1b.jsonl"), [
        agent_entry([{"type": "text", "text": "Inner part done."}], 60),
    ])

    return SessionPaths(session_id=session_id, session_jsonl=session_jsonl,
                        subagents_dir=subagents, project_dir=project)
```

- [ ] **Step 4: Write the failing test**

Create `tests/test_build.py`:

```python
import os
import tempfile
import unittest

from orchestra import constants as C
from orchestra.build import RunBuilder
from orchestra.parent import parse_timestamp
from tests.fixtures import (agent_entry, build_nested_session, build_session, ts,
                            write_jsonl)

NOW_LIVE = parse_timestamp(ts(150))
NOW_DEAD = NOW_LIVE + C.SESSION_LIVE_THRESHOLD_S + 60


class BuildTestCase(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp()
        self.paths = build_session(self.root)

    def build(self, now=NOW_LIVE, live=True):
        builder = RunBuilder(self.paths, now_fn=lambda: now)
        if not live:
            os.utime(self.paths.session_jsonl, (now - 99999, now - 99999))
        else:
            os.utime(self.paths.session_jsonl, (now, now))
        return builder.refresh()


class TestRunAssembly(BuildTestCase):
    def test_finds_all_three_agents(self):
        run = self.build()
        self.assertEqual({a.agent_id for a in run.agents}, {"a1", "a2", "a3"})

    def test_project_path_comes_from_the_transcript(self):
        self.assertEqual(self.build().project_path, r"E:\proj")

    def test_metadata_is_attached(self):
        a1 = self.build().agent("a1")
        self.assertEqual(a1.agent_type, "general-purpose")
        self.assertEqual(a1.description, "Plan the work")
        self.assertEqual(a1.launch_mode, "background")
        self.assertEqual(a1.model, "haiku")

    def test_brief_and_extraction(self):
        a1 = self.build().agent("a1")
        self.assertIn("You are planning", a1.brief)
        self.assertIn("A written plan at PLAN.md", a1.expected_output.text)
        self.assertIn("Deliverable", a1.expected_output.source)

    def test_statuses(self):
        run = self.build()
        self.assertEqual(run.agent("a1").status, C.COMPLETED)
        self.assertEqual(run.agent("a2").status, C.COMPLETED)
        self.assertEqual(run.agent("a3").status, C.RUNNING)

    def test_running_agent_becomes_orphaned_when_session_dies(self):
        run = self.build(now=NOW_DEAD, live=False)
        self.assertEqual(run.agent("a3").status, C.ORPHANED)

    def test_durations(self):
        a1 = self.build().agent("a1")
        self.assertEqual(a1.duration_s, 60.0)
        self.assertIsNone(self.build().agent("a3").duration_s)

    def test_tokens_are_summed_per_agent_and_per_run(self):
        run = self.build()
        self.assertEqual(run.agent("a1").tokens["input"], 100)
        self.assertEqual(run.totals()["tokens"]["input"], 510)

    def test_results_are_captured(self):
        run = self.build()
        self.assertIn("Wrote the plan", run.agent("a1").result)
        self.assertEqual(run.agent("a3").result, "")

    def test_files_written_and_read(self):
        run = self.build()
        self.assertEqual(len(run.agent("a1").files_written), 1)
        self.assertEqual(len(run.agent("a2").files_read), 1)


class TestBatchesAndEdges(BuildTestCase):
    def test_agents_launched_in_one_turn_form_a_batch(self):
        run = self.build()
        waves = {tuple(sorted(b.agent_ids)) for b in run.batches}
        self.assertIn(("a2", "a3"), waves)
        self.assertIn(("a1",), waves)

    def test_spawn_edges_come_from_the_orchestrator(self):
        run = self.build()
        spawn = {(e.src, e.dst) for e in run.edges if e.kind == "spawn"}
        self.assertEqual(spawn, {("main", "a1"), ("main", "a2"), ("main", "a3")})

    def test_artifact_edges_follow_the_plan_file(self):
        run = self.build()
        artifact = {(e.src, e.dst) for e in run.edges if e.kind == "artifact"}
        self.assertEqual(artifact, {("a1", "a2"), ("a1", "a3")})

    def test_handoff_folds_into_the_artifact_edge_for_a2(self):
        run = self.build()
        edge = [e for e in run.edges
                if (e.src, e.dst) == ("a1", "a2") and e.kind == "artifact"][0]
        self.assertIn("handoff", edge.evidence)

    def test_summary_dict_is_json_serializable(self):
        import json
        json.dumps(self.build().to_summary_dict())


class TestIncrementalRefresh(BuildTestCase):
    def test_second_refresh_picks_up_new_activity(self):
        builder = RunBuilder(self.paths, now_fn=lambda: NOW_LIVE)
        os.utime(self.paths.session_jsonl, (NOW_LIVE, NOW_LIVE))
        first = builder.refresh()
        self.assertEqual(first.agent("a3").tokens.get("output", 0), 0)

        path = os.path.join(self.paths.subagents_dir, "agent-a3.jsonl")
        with open(path, "a", encoding="utf-8") as fh:
            import json
            fh.write(json.dumps(agent_entry(
                [{"type": "text", "text": "Implemented part two."}], 145,
                usage={"output_tokens": 33})) + "\n")

        second = builder.refresh()
        self.assertEqual(second.agent("a3").tokens["output"], 33)

    def test_diagnostics_report_unparsable_lines(self):
        path = os.path.join(self.paths.subagents_dir, "agent-a3.jsonl")
        with open(path, "a", encoding="utf-8") as fh:
            fh.write("not json at all\n")
        run = self.build()
        self.assertEqual(run.diagnostics["unparsable_lines"], 1)


class TestNestedAgents(unittest.TestCase):
    """spawnDepth > 1 has no real-world sample yet; this is its only coverage."""

    def setUp(self):
        self.root = tempfile.mkdtemp()
        self.paths = build_nested_session(self.root)

    def build(self):
        os.utime(self.paths.session_jsonl, (NOW_LIVE, NOW_LIVE))
        return RunBuilder(self.paths, now_fn=lambda: NOW_LIVE).refresh()

    def test_both_agents_are_found(self):
        self.assertEqual({a.agent_id for a in self.build().agents}, {"a1", "a1b"})

    def test_nested_agent_records_its_launcher(self):
        run = self.build()
        self.assertIsNone(run.agent("a1").parent_agent_id)
        self.assertEqual(run.agent("a1b").parent_agent_id, "a1")

    def test_spawn_depth_is_preserved(self):
        self.assertEqual(self.build().agent("a1b").spawn_depth, 2)

    def test_spawn_edge_points_from_the_parent_agent(self):
        spawn = {(e.src, e.dst) for e in self.build().edges if e.kind == "spawn"}
        self.assertEqual(spawn, {("main", "a1"), ("a1", "a1b")})

    def test_nested_agent_brief_comes_from_the_inner_launch(self):
        self.assertIn("inner part", self.build().agent("a1b").brief.lower())


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 5: Run the test to verify it fails**

Run: `python -m unittest tests.test_build -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'orchestra.build'`

- [ ] **Step 6: Write `orchestra/build.py`**

```python
"""Assembling a Run from a session's files.

Pure given the file contents: no clock of its own (now_fn is injected), no
network, no writes. Everything the dashboard shows is decided here.
"""

import glob
import os
import time
from typing import Callable, Dict, List, Optional

from orchestra import constants as C
from orchestra.agentlog import AgentDigest
from orchestra.edges import infer_edges
from orchestra.extract import extract_expected_output, extract_objective
from orchestra.locate import SessionPaths
from orchestra.model import Agent, Batch, Run
from orchestra.parent import ParentIndex
from orchestra.status import build_rounds, compute_status
from orchestra.transcript import IncrementalReader


class RunBuilder:
    """Holds the incremental state between polls. One per session."""

    def __init__(self, paths: SessionPaths,
                 now_fn: Callable[[], float] = time.time) -> None:
        self.paths = paths
        self.now_fn = now_fn
        self._reader = IncrementalReader()
        self._parent = ParentIndex()
        self._digests: Dict[str, AgentDigest] = {}
        self._metas: Dict[str, dict] = {}

    def refresh(self) -> Run:
        now = self.now_fn()
        self._parent.ingest(self._reader.read_new(self.paths.session_jsonl))
        self._scan_subagents()
        session_live = self._session_live(now)

        agents = [self._assemble(agent_id, now, session_live)
                  for agent_id in sorted(self._agent_ids())]
        agents = [a for a in agents if a is not None]
        agents.sort(key=lambda a: (a.started_at is None, a.started_at or 0))

        edges, hubs = infer_edges(agents)
        starts = [a.started_at for a in agents if a.started_at is not None]
        ends = [a.ended_at for a in agents if a.ended_at is not None]

        return Run(
            session_id=self.paths.session_id,
            project_path=self._parent.cwd,
            started_at=min(starts) if starts else None,
            ended_at=max(ends) if ends and not session_live else None,
            session_live=session_live,
            agents=agents,
            edges=edges,
            batches=self._batches(agents),
            hub_files=hubs,
            diagnostics=dict(self._reader.diagnostics),
        )

    # -- internals ---------------------------------------------------------

    def _session_live(self, now: float) -> bool:
        try:
            age = now - os.path.getmtime(self.paths.session_jsonl)
        except OSError:
            return False
        return age <= C.SESSION_LIVE_THRESHOLD_S

    def _scan_subagents(self) -> None:
        directory = self.paths.subagents_dir
        if not os.path.isdir(directory):
            return
        for meta_path in glob.glob(os.path.join(directory, "agent-*.meta.json")):
            agent_id = os.path.basename(meta_path)[len("agent-"):-len(".meta.json")]
            meta = self._reader.read_json(meta_path)
            if meta:
                self._metas[agent_id] = meta
        for log_path in glob.glob(os.path.join(directory, "agent-*.jsonl")):
            agent_id = os.path.basename(log_path)[len("agent-"):-len(".jsonl")]
            entries = self._reader.read_new(log_path)
            if not entries:
                continue
            digest = self._digests.setdefault(agent_id, AgentDigest())
            digest.ingest(entries)
            # A nested agent's launches live in its own transcript.
            self._parent.ingest(entries)

    def _agent_ids(self) -> List[str]:
        ids = set(self._metas) | set(self._digests)
        for result in self._parent.results.values():
            if result.agent_id:
                ids.add(result.agent_id)
        return sorted(ids)

    def _tool_use_id_for(self, agent_id: str) -> str:
        meta = self._metas.get(agent_id) or {}
        if meta.get("toolUseId"):
            return str(meta["toolUseId"])
        for result in self._parent.results.values():
            if result.agent_id == agent_id:
                return result.tool_use_id
        for notes in self._parent.notifications.get(agent_id, []):
            if notes.tool_use_id:
                return notes.tool_use_id
        return ""

    def _assemble(self, agent_id: str, now: float, session_live: bool) -> Optional[Agent]:
        meta = self._metas.get(agent_id) or {}
        digest = self._digests.get(agent_id) or AgentDigest()
        tool_use_id = self._tool_use_id_for(agent_id)
        launch = self._parent.launches.get(tool_use_id)
        result = self._parent.results.get(tool_use_id)
        notifications = self._parent.notifications.get(agent_id, [])

        rounds = build_rounds(launch, result, notifications, digest)
        status = compute_status(rounds, digest, now, session_live)

        brief = launch.prompt if launch else ""
        description = str(meta.get("description") or (launch.description if launch else ""))
        final_result = ""
        for round_ in reversed(rounds):
            if round_.result:
                final_result = round_.result
                break
        if not final_result and rounds and rounds[-1].ended_at is not None:
            final_result = digest.final_text

        shape = meta.get("requestShape")
        launch_mode = "background" if shape == "background" else (
            result.launch_mode if result else "inline")

        log_path = os.path.join(self.paths.subagents_dir,
                                "agent-{}.jsonl".format(agent_id))
        return Agent(
            agent_id=agent_id,
            tool_use_id=tool_use_id,
            parent_agent_id=launch.launcher_agent_id if launch else None,
            spawn_depth=int(meta.get("spawnDepth") or 1),
            agent_type=str(meta.get("agentType") or ""),
            description=description,
            model=str(meta.get("model") or (launch.model if launch else "")
                      or digest.model),
            launch_mode=launch_mode,
            brief=brief,
            objective=extract_objective(brief, description),
            expected_output=extract_expected_output(brief, description),
            status=status,
            rounds=rounds,
            last_activity_at=digest.last_activity_at,
            result=final_result,
            tokens=dict(digest.tokens),
            tool_calls=list(digest.tool_calls),
            files_written=list(digest.files_written),
            files_read=list(digest.files_read),
            transcript_path=log_path,
        )

    def _batches(self, agents: List[Agent]) -> List[Batch]:
        by_tool_use = {a.tool_use_id: a.agent_id for a in agents if a.tool_use_id}
        grouped: Dict[str, Batch] = {}
        for launch in self._parent.launches.values():
            agent_id = by_tool_use.get(launch.tool_use_id)
            if not agent_id:
                continue
            batch = grouped.setdefault(
                launch.turn_uuid,
                Batch(turn_uuid=launch.turn_uuid, launched_at=launch.launched_at))
            batch.agent_ids.append(agent_id)
            if launch.launched_at is not None:
                if batch.launched_at is None or launch.launched_at < batch.launched_at:
                    batch.launched_at = launch.launched_at
        batches = list(grouped.values())
        for batch in batches:
            batch.agent_ids.sort()
        batches.sort(key=lambda b: (b.launched_at is None, b.launched_at or 0))
        return batches
```

- [ ] **Step 7: Run the test to verify it passes**

Run: `python -m unittest discover -s tests -t . -v`
Expected: PASS, full suite.

If `test_artifact_edges_follow_the_plan_file` fails with an empty set, check `normalize_path` — the fixture writes `E:\proj\PLAN.md` and reads the same path, so a failure here means separator or case normalization regressed.

- [ ] **Step 8: Sanity-check against your real data (manual, not a test)**

```bash
python -c "import os,json;from orchestra.locate import find_session;from orchestra.build import RunBuilder;p=find_session(os.environ['CLAUDE_CODE_SESSION_ID']);r=RunBuilder(p).refresh();print(json.dumps(r.totals(),indent=1));print([(a.agent_id,a.status,a.description) for a in r.agents])"
```

Expected: a real agent count and statuses for your current session. This is the first moment the tool does something useful; if the counts look wrong, stop and fix before building the server on top.

- [ ] **Step 9: Commit**

```bash
git add orchestra/build.py orchestra/model.py tests/fixtures.py tests/test_build.py tests/test_model.py
git commit -m "feat: assemble complete Run from session transcripts"
```

---

## Task 11: The HTTP server

**Files:**
- Create: `orchestra/service.py`, `orchestra/http.py`
- Test: `tests/test_http.py`

**Interfaces:**
- Consumes: `RunBuilder` (Task 10), `find_session`, `list_sessions` (Task 5).
- Produces: `OrchestraService(root, token, default_session, now_fn)` with `.run_summary(session_id)`, `.agent_detail(session_id, agent_id)`, `.session_list(session_id)`; `make_server(service, port) -> ThreadingHTTPServer`; `serve(service, port) -> (server, thread)`.

`service.py` is separate from `http.py` so every routing decision can be tested without sockets, and every socket concern can be tested without touching the analysis.

- [ ] **Step 1: Write the failing test**

Create `tests/test_http.py`:

```python
import json
import tempfile
import unittest
import urllib.error
import urllib.request

from orchestra.http import serve
from orchestra.parent import parse_timestamp
from orchestra.service import OrchestraService
from tests.fixtures import build_session, ts

TOKEN = "test-token-123"


class HttpTestCase(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp()
        build_session(self.root, "s1")
        service = OrchestraService(root=self.root, token=TOKEN, default_session="s1",
                                   now_fn=lambda: parse_timestamp(ts(150)))
        self.server, self.thread = serve(service, port=0)
        self.base = "http://127.0.0.1:{}".format(self.server.server_port)
        self.addCleanup(self.server.shutdown)

    def get(self, path, token=TOKEN, host=None):
        url = self.base + path
        if token is not None:
            url += ("&" if "?" in path else "?") + "k=" + token
        request = urllib.request.Request(url)
        if host:
            request.add_header("Host", host)
        return urllib.request.urlopen(request, timeout=5)

    def get_json(self, path, **kw):
        return json.loads(self.get(path, **kw).read().decode("utf-8"))


class TestAuth(HttpTestCase):
    def test_missing_token_is_rejected(self):
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            self.get("/api/run", token=None)
        self.assertEqual(ctx.exception.code, 403)

    def test_wrong_token_is_rejected(self):
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            self.get("/api/run", token="nope")
        self.assertEqual(ctx.exception.code, 403)

    def test_non_loopback_host_header_is_rejected(self):
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            self.get("/api/run", host="evil.example.com")
        self.assertEqual(ctx.exception.code, 403)

    def test_cross_site_origin_is_rejected(self):
        request = urllib.request.Request(
            self.base + "/api/run?k=" + TOKEN,
            headers={"Origin": "https://evil.example.com"})
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            urllib.request.urlopen(request, timeout=5)
        self.assertEqual(ctx.exception.code, 403)

    def test_loopback_origin_is_allowed(self):
        request = urllib.request.Request(
            self.base + "/api/run?k=" + TOKEN,
            headers={"Origin": self.base})
        self.assertEqual(urllib.request.urlopen(request, timeout=5).status, 200)

    def test_static_page_does_not_require_a_token(self):
        self.assertEqual(self.get("/", token=None).status, 200)


class TestApi(HttpTestCase):
    def test_run_returns_the_default_session(self):
        data = self.get_json("/api/run")
        self.assertEqual(data["session_id"], "s1")
        self.assertEqual(data["totals"]["agents"], 3)
        self.assertEqual(len(data["agents"]), 3)

    def test_run_agents_are_light(self):
        agent = self.get_json("/api/run")["agents"][0]
        self.assertNotIn("brief", agent)

    def test_agent_detail_is_heavy(self):
        detail = self.get_json("/api/agent/a1")
        self.assertIn("You are planning", detail["brief"])
        self.assertIn("PLAN.md", detail["expected_output"])
        self.assertIn("tool_calls", detail)

    def test_unknown_agent_is_404(self):
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            self.get("/api/agent/nope")
        self.assertEqual(ctx.exception.code, 404)

    def test_unknown_session_is_404(self):
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            self.get("/api/run?session=missing")
        self.assertEqual(ctx.exception.code, 404)

    def test_sessions_list(self):
        data = self.get_json("/api/sessions")
        self.assertEqual(data["sessions"][0]["session_id"], "s1")
        self.assertEqual(data["sessions"][0]["agent_count"], 3)

    def test_health_needs_no_token(self):
        self.assertEqual(self.get("/api/health", token=None).status, 200)

    def test_unknown_route_is_404(self):
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            self.get("/api/nothing")
        self.assertEqual(ctx.exception.code, 404)


class TestStatic(HttpTestCase):
    def test_index_is_html(self):
        response = self.get("/", token=None)
        self.assertIn("text/html", response.headers["Content-Type"])

    def test_path_traversal_is_refused(self):
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            self.get("/../../../etc/passwd", token=None)
        self.assertIn(ctx.exception.code, (403, 404))


class TestBuilderReuse(unittest.TestCase):
    def test_same_session_reuses_one_builder(self):
        root = tempfile.mkdtemp()
        build_session(root, "s1")
        service = OrchestraService(root=root, token=TOKEN, default_session="s1",
                                   now_fn=lambda: parse_timestamp(ts(150)))
        service.run_summary("s1")
        first = service._builders["s1"]
        service.run_summary("s1")
        self.assertIs(service._builders["s1"], first)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `python -m unittest tests.test_http -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'orchestra.http'`

- [ ] **Step 3: Write `orchestra/service.py`**

```python
"""What the API answers. No sockets here."""

import os
import time
from typing import Any, Callable, Dict, List, Optional

from orchestra.build import RunBuilder
from orchestra.locate import find_session, list_sessions


class NotFound(Exception):
    """Raised when a session or agent does not exist."""


class OrchestraService:
    def __init__(self, root: Optional[str] = None, token: str = "",
                 default_session: str = "",
                 now_fn: Callable[[], float] = time.time) -> None:
        self.root = root
        self.token = token
        self.default_session = default_session
        self.now_fn = now_fn
        self._builders: Dict[str, RunBuilder] = {}

    def _builder(self, session_id: str) -> RunBuilder:
        session_id = session_id or self.default_session
        if session_id not in self._builders:
            paths = find_session(session_id, root=self.root)
            if paths is None:
                raise NotFound("unknown session: {}".format(session_id))
            self._builders[session_id] = RunBuilder(paths, now_fn=self.now_fn)
        return self._builders[session_id]

    def run_summary(self, session_id: str = "") -> Dict[str, Any]:
        return self._builder(session_id).refresh().to_summary_dict()

    def agent_detail(self, agent_id: str, session_id: str = "") -> Dict[str, Any]:
        run = self._builder(session_id).refresh()
        agent = run.agent(agent_id)
        if agent is None:
            raise NotFound("unknown agent: {}".format(agent_id))
        return agent.to_detail_dict()

    def session_list(self, session_id: str = "") -> Dict[str, Any]:
        builder = self._builder(session_id)
        sessions: List[Dict[str, Any]] = []
        for info in list_sessions(builder.paths.project_dir):
            sessions.append({"session_id": info.session_id,
                             "modified_at": info.modified_at,
                             "agent_count": info.agent_count,
                             "size_bytes": info.size_bytes})
        return {"project_dir": os.path.basename(builder.paths.project_dir),
                "current": builder.paths.session_id,
                "sessions": sessions}
```

- [ ] **Step 4: Write `orchestra/http.py`**

```python
"""The local server. Loopback only, token-gated, no egress."""

import json
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Dict, Optional, Tuple
from urllib.parse import parse_qs, unquote, urlparse

from orchestra import constants as C
from orchestra.service import NotFound, OrchestraService

STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")

_CONTENT_TYPES = {".html": "text/html; charset=utf-8",
                  ".js": "text/javascript; charset=utf-8",
                  ".css": "text/css; charset=utf-8"}

_LOOPBACK = ("127.0.0.1", "localhost", "[::1]", "::1")


def _host_is_loopback(header: Optional[str]) -> bool:
    if not header:
        return False
    host = header.rsplit(":", 1)[0] if header.count(":") == 1 else header
    return host.strip("[]") in [h.strip("[]") for h in _LOOPBACK]


def _origin_is_allowed(header: Optional[str]) -> bool:
    """A cross-site page must not be able to read the dashboard's JSON.

    No Origin at all is fine: that is a same-origin navigation or a direct
    fetch. An Origin naming somewhere other than loopback is a cross-site
    read attempt and is refused.
    """
    if not header or header == "null":
        return True
    return _host_is_loopback(urlparse(header).netloc)


def make_handler(service: OrchestraService, state: Dict[str, float]):
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"
        server_version = "Orchestra"

        def log_message(self, fmt, *args):  # silence the default stderr spam
            pass

        # -- helpers ----------------------------------------------------
        def _send(self, code: int, body: bytes, content_type: str) -> None:
            self.send_response(code)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.wfile.write(body)

        def _json(self, code: int, payload: Dict[str, Any]) -> None:
            self._send(code, json.dumps(payload).encode("utf-8"),
                       "application/json; charset=utf-8")

        def _error(self, code: int, message: str) -> None:
            self._json(code, {"error": message})

        def _authorized(self, query: Dict[str, list]) -> bool:
            supplied = (query.get("k", [""])[0]
                        or self.headers.get("X-Orchestra-Token", ""))
            return bool(service.token) and supplied == service.token

        # -- routing ----------------------------------------------------
        def do_GET(self) -> None:  # noqa: N802 (stdlib naming)
            state["last_request"] = time.time()
            if not _host_is_loopback(self.headers.get("Host")):
                self._error(403, "non-loopback host")
                return
            if not _origin_is_allowed(self.headers.get("Origin")):
                self._error(403, "cross-site origin")
                return
            parsed = urlparse(self.path)
            path = unquote(parsed.path)
            query = parse_qs(parsed.query)
            if path.startswith("/api/"):
                self._api(path, query)
            else:
                self._static(path)

        def _api(self, path: str, query: Dict[str, list]) -> None:
            if path == "/api/health":
                self._json(200, {"ok": True})
                return
            if not self._authorized(query):
                self._error(403, "bad or missing token")
                return
            session = query.get("session", [""])[0]
            try:
                if path == "/api/run":
                    self._json(200, service.run_summary(session))
                elif path.startswith("/api/agent/"):
                    agent_id = path[len("/api/agent/"):]
                    self._json(200, service.agent_detail(agent_id, session))
                elif path == "/api/sessions":
                    self._json(200, service.session_list(session))
                else:
                    self._error(404, "no such route")
            except NotFound as exc:
                self._error(404, str(exc))

        def _static(self, path: str) -> None:
            name = "index.html" if path in ("/", "") else path.lstrip("/")
            target = os.path.normpath(os.path.join(STATIC_DIR, name))
            if not target.startswith(STATIC_DIR + os.sep) or not os.path.isfile(target):
                self._error(404, "not found")
                return
            extension = os.path.splitext(target)[1]
            if extension not in _CONTENT_TYPES:
                self._error(403, "unsupported asset")
                return
            with open(target, "rb") as fh:
                self._send(200, fh.read(), _CONTENT_TYPES[extension])

    return Handler


def make_server(service: OrchestraService, port: int) -> ThreadingHTTPServer:
    state = {"last_request": time.time()}
    server = ThreadingHTTPServer(("127.0.0.1", port), make_handler(service, state))
    server.orchestra_state = state  # type: ignore[attr-defined]
    return server


def serve(service: OrchestraService, port: int = C.DEFAULT_PORT
          ) -> Tuple[ThreadingHTTPServer, threading.Thread]:
    """Start on a background thread. Returns (server, thread)."""
    server = make_server(service, port)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread


def start_idle_watchdog(server: ThreadingHTTPServer,
                        idle_seconds: int = C.IDLE_SHUTDOWN_S) -> threading.Thread:
    """Shut down after a long silence so no server is left running for days."""
    state = server.orchestra_state  # type: ignore[attr-defined]

    def watch() -> None:
        while True:
            time.sleep(30)
            if time.time() - state["last_request"] > idle_seconds:
                server.shutdown()
                return

    thread = threading.Thread(target=watch, daemon=True)
    thread.start()
    return thread
```

- [ ] **Step 5: Create a placeholder `index.html` so the static tests pass**

The real page arrives in Task 12; the server tests need a file to serve now.

```bash
mkdir -p orchestra/static
printf '<!doctype html>\n<title>Orchestra</title>\n<p>Loading...</p>\n' > orchestra/static/index.html
```

- [ ] **Step 6: Run the test to verify it passes**

Run: `python -m unittest tests.test_http -v`
Expected: PASS, 17 tests.

- [ ] **Step 7: Commit**

```bash
git add orchestra/service.py orchestra/http.py orchestra/static/index.html tests/test_http.py
git commit -m "feat: add loopback-only token-gated HTTP server"
```

---

## Task 12: Page shell, styling, and the fleet timeline

**Files:**
- Create: `orchestra/static/index.html`, `orchestra/static/style.css`, `orchestra/static/app.js`
- Test: `tests/test_static_assets.py`

**Interfaces:**
- Consumes: `/api/run`, `/api/sessions` (Task 11).
- Produces: the page shell, `renderTimeline(run, svg)`, `renderHeader(run)`, `renderHealth(run)` in `app.js`; a `<div id="graph">` and `<aside id="drawer">` left empty for Task 13.

The browser UI cannot be unit-tested without adding dependencies, which the constraints forbid. What *can* be tested — and is the constraint most likely to be violated by accident — is that no asset reaches for the network. `tests/test_static_assets.py` enforces that, and the visual behaviour is verified by explicit manual steps with stated expected results.

- [ ] **Step 1: Write the failing test**

Create `tests/test_static_assets.py`:

```python
import os
import re
import unittest

STATIC = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                      "orchestra", "static")

EXTERNAL = re.compile(r"""(?:src|href)\s*=\s*["'](?:https?:)?//""", re.IGNORECASE)
FETCH_ABSOLUTE = re.compile(r"""fetch\(\s*["'](?:https?:)?//""", re.IGNORECASE)
LOCAL_REF = re.compile(r"""(?:src|href)\s*=\s*["']([^"'#]+)["']""")


def read(name):
    with open(os.path.join(STATIC, name), encoding="utf-8") as fh:
        return fh.read()


class TestNoNetworkEgress(unittest.TestCase):
    """The local-only guarantee is a hard constraint; this is its enforcement."""

    def test_no_external_src_or_href(self):
        for name in ("index.html", "app.js", "style.css"):
            self.assertIsNone(EXTERNAL.search(read(name)),
                              "{} reaches an external host".format(name))

    def test_no_absolute_fetch(self):
        self.assertIsNone(FETCH_ABSOLUTE.search(read("app.js")))

    def test_no_font_imports(self):
        self.assertNotIn("@import", read("style.css"))


class TestLocalReferencesResolve(unittest.TestCase):
    def test_every_referenced_file_exists(self):
        for ref in LOCAL_REF.findall(read("index.html")):
            self.assertTrue(os.path.isfile(os.path.join(STATIC, ref)),
                            "missing asset: {}".format(ref))


class TestPageStructure(unittest.TestCase):
    def test_required_mount_points_exist(self):
        html = read("index.html")
        for element_id in ("totals", "health", "timeline", "graph", "drawer",
                           "session-picker", "diagnostics"):
            self.assertIn('id="{}"'.format(element_id), html)

    def test_theme_is_defined_for_light_and_dark(self):
        css = read("style.css")
        self.assertIn(":root", css)
        self.assertIn("prefers-color-scheme: dark", css)

    def test_status_classes_exist_for_every_status(self):
        css = read("style.css")
        for status in ("running", "completed", "failed", "stalled",
                       "orphaned", "unknown"):
            self.assertIn(".s-{}".format(status), css)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `python -m unittest tests.test_static_assets -v`
Expected: FAIL — the placeholder `index.html` has no mount points and `style.css` does not exist.

- [ ] **Step 3: Write `orchestra/static/index.html`**

```html
<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Orchestra</title>
<link rel="stylesheet" href="style.css">
</head>
<body>
<header>
  <div class="bar">
    <h1>Orchestra</h1>
    <select id="session-picker" aria-label="Session"></select>
    <button id="live-toggle" type="button" aria-pressed="true">Live</button>
    <span id="conn" class="conn"></span>
  </div>
  <div id="totals" class="totals"></div>
</header>

<div id="health" class="health" hidden></div>

<nav class="tabs" role="tablist">
  <button type="button" class="tab active" data-view="timeline" role="tab">Timeline</button>
  <button type="button" class="tab" data-view="graph" role="tab">Graph</button>
</nav>

<main>
  <section id="view-timeline" class="view">
    <svg id="timeline" role="img" aria-label="Agent timeline"></svg>
  </section>
  <section id="view-graph" class="view" hidden>
    <svg id="graph" role="img" aria-label="Agent dependency graph"></svg>
    <div id="edge-evidence" class="evidence" hidden></div>
  </section>
</main>

<aside id="drawer" class="drawer" hidden aria-label="Agent detail"></aside>
<footer id="diagnostics" class="diagnostics"></footer>

<script src="app.js"></script>
</body>
</html>
```

- [ ] **Step 4: Write `orchestra/static/style.css`**

```css
:root {
  color-scheme: light dark;
  --bg: #fbfbfa;
  --panel: #ffffff;
  --ink: #1a1a19;
  --muted: #6b6b66;
  --line: #e3e3df;
  --accent: #3b5bdb;
  --running: #1c7ed6;
  --completed: #2f9e44;
  --failed: #e03131;
  --stalled: #e8950c;
  --orphaned: #868e96;
  --unknown: #adb5bd;
  --font: ui-sans-serif, system-ui, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
  --mono: ui-monospace, "Cascadia Mono", Consolas, "Courier New", monospace;
}

@media (prefers-color-scheme: dark) {
  :root {
    --bg: #17171a;
    --panel: #1f1f23;
    --ink: #e8e8e6;
    --muted: #9a9a95;
    --line: #32323a;
    --accent: #748ffc;
  }
}

* { box-sizing: border-box; }

body {
  margin: 0;
  background: var(--bg);
  color: var(--ink);
  font: 14px/1.5 var(--font);
}

header { border-bottom: 1px solid var(--line); padding: 12px 16px; }
.bar { display: flex; align-items: center; gap: 12px; flex-wrap: wrap; }
h1 { font-size: 16px; margin: 0; letter-spacing: 0.02em; }
select, button { font: inherit; color: inherit; background: var(--panel);
  border: 1px solid var(--line); border-radius: 6px; padding: 4px 8px; }
button { cursor: pointer; }
button[aria-pressed="false"] { opacity: 0.55; }
.conn { color: var(--muted); font-size: 12px; }

.totals { margin-top: 8px; color: var(--muted); display: flex;
  gap: 16px; flex-wrap: wrap; font-variant-numeric: tabular-nums; }
.totals strong { color: var(--ink); font-weight: 600; }

.health { margin: 12px 16px; padding: 10px 12px; border-radius: 8px;
  border: 1px solid var(--stalled); background: color-mix(in srgb, var(--stalled) 10%, transparent); }
.health ul { margin: 6px 0 0; padding-left: 18px; }

.tabs { display: flex; gap: 4px; padding: 10px 16px 0; }
.tab { border-bottom-left-radius: 0; border-bottom-right-radius: 0; }
.tab.active { border-color: var(--accent); color: var(--accent); }

main { padding: 12px 16px 24px; }
.view svg { width: 100%; background: var(--panel);
  border: 1px solid var(--line); border-radius: 8px; }

/* Status is carried by colour AND by a text label on every bar, so the view
   survives colourblindness and greyscale screenshots. */
.s-running   { fill: var(--running); }
.s-completed { fill: var(--completed); }
.s-failed    { fill: var(--failed); }
.s-stalled   { fill: var(--stalled); }
.s-orphaned  { fill: var(--orphaned); }
.s-unknown   { fill: var(--unknown); }

.row-label { fill: var(--ink); font-size: 11px; }
.row-meta { fill: var(--muted); font-size: 10px; }
.axis { stroke: var(--line); }
.axis-text { fill: var(--muted); font-size: 10px; }
.batch-band { fill: var(--accent); opacity: 0.06; }
.bar { cursor: pointer; }
.bar:hover { opacity: 0.8; }
.bar-open { stroke: var(--running); stroke-dasharray: 3 3; fill-opacity: 0.35; }

.drawer { position: fixed; top: 0; right: 0; width: min(560px, 92vw);
  height: 100vh; overflow-y: auto; background: var(--panel);
  border-left: 1px solid var(--line); padding: 16px; box-shadow: -8px 0 24px rgba(0,0,0,0.12); }
.drawer h2 { font-size: 15px; margin: 0 0 4px; }
.drawer dl { display: grid; grid-template-columns: max-content 1fr;
  gap: 4px 12px; margin: 12px 0; }
.drawer dt { color: var(--muted); }
.drawer pre { background: var(--bg); border: 1px solid var(--line);
  border-radius: 6px; padding: 10px; overflow-x: auto;
  white-space: pre-wrap; word-break: break-word; font: 12px/1.5 var(--mono); }
.source-note { color: var(--muted); font-size: 11px; font-style: italic; }
.close { float: right; }

.evidence { margin-top: 12px; padding: 10px 12px; background: var(--panel);
  border: 1px solid var(--line); border-radius: 8px; }
.diagnostics { padding: 0 16px 24px; color: var(--muted); font-size: 12px; }

@media (max-width: 700px) {
  .drawer { width: 100vw; }
}
```

- [ ] **Step 5: Write `orchestra/static/app.js` (shell, polling, header, health, timeline)**

```javascript
"use strict";

const TOKEN = new URLSearchParams(location.search).get("k") || "";
const POLL_MS = 2000;

const state = {
  run: null,
  sessionId: new URLSearchParams(location.search).get("session") || "",
  live: true,
  view: "timeline",
  backoff: POLL_MS,
  selected: null,
};

const $ = (id) => document.getElementById(id);

function api(path) {
  const join = path.includes("?") ? "&" : "?";
  const session = state.sessionId ? "&session=" + encodeURIComponent(state.sessionId) : "";
  return fetch(path + join + "k=" + encodeURIComponent(TOKEN) + session)
    .then((response) => {
      if (!response.ok) throw new Error("HTTP " + response.status);
      return response.json();
    });
}

function fmtDuration(seconds) {
  if (seconds === null || seconds === undefined) return "—";
  if (seconds < 60) return seconds.toFixed(0) + "s";
  const m = Math.floor(seconds / 60);
  const s = Math.round(seconds % 60);
  return m + "m " + (s < 10 ? "0" : "") + s + "s";
}

function fmtTokens(tokens) {
  const total = Object.values(tokens || {}).reduce((a, b) => a + b, 0);
  if (total > 1000000) return (total / 1000000).toFixed(1) + "M";
  if (total > 1000) return (total / 1000).toFixed(1) + "k";
  return String(total);
}

function svgEl(name, attrs, text) {
  const node = document.createElementNS("http://www.w3.org/2000/svg", name);
  for (const key in attrs) node.setAttribute(key, attrs[key]);
  if (text !== undefined) node.textContent = text;
  return node;
}

// ---------------------------------------------------------------- header

function renderHeader(run) {
  const t = run.totals;
  $("totals").innerHTML = "";
  const parts = [
    ["agents", t.agents],
    ["running", t.running],
    ["done", t.completed],
    ["failed", t.failed + t.orphaned],
    ["tokens", fmtTokens(t.tokens)],
    ["wall", fmtDuration(t.wall_time_s)],
  ];
  for (const [label, value] of parts) {
    const span = document.createElement("span");
    span.innerHTML = "<strong>" + value + "</strong> " + label;
    $("totals").appendChild(span);
  }
  $("conn").textContent = run.session_live ? "" : "session ended";
}

function renderHealth(run) {
  const trouble = run.agents.filter((a) =>
    ["stalled", "failed", "orphaned"].includes(a.status));
  const box = $("health");
  if (!trouble.length) { box.hidden = true; return; }
  box.hidden = false;
  box.innerHTML = "<strong>" + trouble.length + " agent(s) need attention</strong>";
  const list = document.createElement("ul");
  for (const agent of trouble) {
    const item = document.createElement("li");
    item.textContent = agent.status.toUpperCase() + " — " +
      (agent.description || agent.agent_id);
    item.style.cursor = "pointer";
    item.onclick = () => openDrawer(agent.agent_id);
    list.appendChild(item);
  }
  box.appendChild(list);
}

function renderDiagnostics(run) {
  const d = run.diagnostics || {};
  const bad = (d.unparsable_lines || 0) + (d.torn_reads || 0);
  $("diagnostics").textContent = bad
    ? bad + " transcript line(s) could not be parsed; the view may be incomplete."
    : "";
}

// -------------------------------------------------------------- timeline

const ROW_H = 26;
const LEFT = 190;
const PAD = 16;

function timeWindow(run) {
  let min = Infinity;
  let max = -Infinity;
  for (const agent of run.agents) {
    if (agent.started_at !== null) min = Math.min(min, agent.started_at);
    const end = agent.ended_at !== null ? agent.ended_at : agent.last_activity_at;
    if (end) max = Math.max(max, end);
  }
  if (!isFinite(min)) return [0, 1];
  if (!isFinite(max) || max <= min) max = min + 1;
  return [min, max];
}

function renderTimeline(run) {
  const svg = $("timeline");
  svg.innerHTML = "";
  const agents = run.agents;
  const width = svg.clientWidth || 900;
  const height = PAD * 2 + Math.max(1, agents.length) * ROW_H + 20;
  svg.setAttribute("height", height);
  svg.setAttribute("viewBox", "0 0 " + width + " " + height);

  const [t0, t1] = timeWindow(run);
  const plot = width - LEFT - PAD;
  const x = (t) => LEFT + ((t - t0) / (t1 - t0)) * plot;
  const rowOf = {};
  agents.forEach((agent, i) => { rowOf[agent.agent_id] = i; });

  // Batch bands sit behind the bars: they are what shows a parallel wave.
  for (const batch of run.batches || []) {
    const rows = batch.agent_ids.map((id) => rowOf[id]).filter((r) => r !== undefined);
    if (rows.length < 2) continue;
    const top = PAD + Math.min(...rows) * ROW_H;
    const tall = (Math.max(...rows) - Math.min(...rows) + 1) * ROW_H;
    svg.appendChild(svgEl("rect", {
      x: LEFT - 4, y: top, width: plot + 8, height: tall, class: "batch-band",
      rx: 4,
    }));
  }

  for (let i = 0; i <= 4; i++) {
    const t = t0 + ((t1 - t0) * i) / 4;
    svg.appendChild(svgEl("line", {
      x1: x(t), y1: PAD - 6, x2: x(t), y2: height - PAD, class: "axis",
    }));
    svg.appendChild(svgEl("text", {
      x: x(t) + 3, y: height - PAD + 12, class: "axis-text",
    }, fmtDuration(t - t0)));
  }

  agents.forEach((agent, i) => {
    const y = PAD + i * ROW_H;
    const label = agent.description || agent.agent_id;
    svg.appendChild(svgEl("text", { x: 0, y: y + 12, class: "row-label" },
      label.length > 28 ? label.slice(0, 27) + "…" : label));
    svg.appendChild(svgEl("text", { x: 0, y: y + 22, class: "row-meta" },
      agent.agent_type + " · " + (agent.model || "?")));

    const rounds = agent.rounds.length ? agent.rounds
      : [{ started_at: agent.started_at, ended_at: agent.ended_at }];
    for (const round of rounds) {
      const start = round.started_at !== null ? round.started_at : t0;
      const end = round.ended_at !== null ? round.ended_at
        : (agent.last_activity_at || t1);
      const bx = x(start);
      const bw = Math.max(3, x(Math.max(end, start)) - bx);
      const bar = svgEl("rect", {
        x: bx, y: y + 4, width: bw, height: ROW_H - 12, rx: 3,
        class: "bar s-" + agent.status + (round.ended_at === null ? " bar-open" : ""),
      });
      bar.appendChild(svgEl("title", {},
        label + " — " + agent.status + " — " + fmtDuration(agent.duration_s)));
      bar.onclick = () => openDrawer(agent.agent_id);
      svg.appendChild(bar);
    }
    // The status word is drawn, not only coloured.
    svg.appendChild(svgEl("text", {
      x: x(agent.started_at !== null ? agent.started_at : t0) + 4,
      y: y + 16, class: "row-meta",
    }, agent.status));
  });
}

// ------------------------------------------------------------ view state

function setView(view) {
  state.view = view;
  $("view-timeline").hidden = view !== "timeline";
  $("view-graph").hidden = view !== "graph";
  for (const tab of document.querySelectorAll(".tab")) {
    tab.classList.toggle("active", tab.dataset.view === view);
  }
  render();
}

function render() {
  if (!state.run) return;
  renderHeader(state.run);
  renderHealth(state.run);
  renderDiagnostics(state.run);
  if (state.view === "timeline") renderTimeline(state.run);
  else renderGraph(state.run);
}

async function poll() {
  try {
    state.run = await api("/api/run");
    state.backoff = POLL_MS;
    $("conn").textContent = state.run.session_live ? "" : "session ended";
    render();
  } catch (err) {
    $("conn").textContent = "reconnecting…";
    state.backoff = Math.min(state.backoff * 2, 30000);
  }
  if (state.live) setTimeout(poll, state.backoff);
}

async function loadSessions() {
  try {
    const data = await api("/api/sessions");
    const picker = $("session-picker");
    picker.innerHTML = "";
    for (const session of data.sessions) {
      const option = document.createElement("option");
      option.value = session.session_id;
      option.textContent = session.session_id.slice(0, 8) + " · " +
        session.agent_count + " agents" +
        (session.session_id === data.current ? " (current)" : "");
      picker.appendChild(option);
    }
    picker.value = state.sessionId || data.current;
  } catch (err) { /* picker is optional; the run view still works */ }
}

function init() {
  $("live-toggle").onclick = (event) => {
    state.live = !state.live;
    event.target.setAttribute("aria-pressed", String(state.live));
    event.target.textContent = state.live ? "Live" : "Paused";
    if (state.live) poll();
  };
  $("session-picker").onchange = (event) => {
    state.sessionId = event.target.value;
    state.run = null;
    poll();
  };
  for (const tab of document.querySelectorAll(".tab")) {
    tab.onclick = () => setView(tab.dataset.view);
  }
  window.addEventListener("resize", () => render());
  loadSessions();
  poll();
}

document.addEventListener("DOMContentLoaded", init);
```

Note: `renderGraph` and `openDrawer` are referenced here and defined in Task 13. Until then the Graph tab and drawer clicks throw a `ReferenceError` in the console — expected, and fixed by the next task.

- [ ] **Step 6: Run the test to verify it passes**

Run: `python -m unittest tests.test_static_assets -v`
Expected: PASS, 7 tests.

- [ ] **Step 7: Verify the timeline visually (manual)**

```bash
python -c "import secrets,os;from orchestra.service import OrchestraService;from orchestra.http import serve;s=OrchestraService(token='dev',default_session=os.environ['CLAUDE_CODE_SESSION_ID']);srv,_=serve(s,0);print('http://127.0.0.1:%d/?k=dev'%srv.server_port);input('enter to stop')"
```

Open the printed URL. Expected: the header shows a real agent count; one bar per agent; agents launched together share a tinted band; completed bars are solid, running bars are dashed; the status word is legible on each row; clicking a bar throws `ReferenceError: openDrawer is not defined` in the console (Task 13 fixes this).

- [ ] **Step 8: Commit**

```bash
git add orchestra/static/ tests/test_static_assets.py
git commit -m "feat: add dashboard shell, theming, and fleet timeline"
```

---

## Task 13: The handoff graph and the agent drawer

**Files:**
- Modify: `orchestra/static/app.js` (append), `orchestra/static/style.css` (append)
- Test: `tests/test_static_assets.py` (extend)

**Interfaces:**
- Consumes: `/api/agent/<id>` (Task 11), the `state` and `svgEl` helpers from Task 12.
- Produces: `renderGraph(run)`, `openDrawer(agentId)`, `layoutGraph(run)` — the two functions Task 12 left dangling.

Layout is a small Sugiyama: rank nodes by longest path over *exact* edges only (an inferred handoff must not move a node's rank), order within each rank by start time, then two barycenter sweeps to cut crossings.

- [ ] **Step 1: Extend the failing test**

Append to `tests/test_static_assets.py`:

```python
class TestGraphAndDrawerPresent(unittest.TestCase):
    def test_functions_task_twelve_referenced_are_defined(self):
        js = read("app.js")
        for name in ("function renderGraph", "function openDrawer",
                     "function layoutGraph"):
            self.assertIn(name, js, "{} is missing".format(name))

    def test_inferred_edges_are_styled_differently(self):
        self.assertIn("edge-inferred", read("app.js"))
        self.assertIn(".edge-inferred", read("style.css"))

    def test_drawer_shows_the_extraction_source(self):
        self.assertIn("expected_output_source", read("app.js"))
```

Run: `python -m unittest tests.test_static_assets -v`
Expected: FAIL — three new failures.

- [ ] **Step 2: Append the graph styles to `orchestra/static/style.css`**

```css
.node rect { fill: var(--panel); stroke: var(--line); cursor: pointer; }
.node:hover rect { stroke: var(--accent); }
.node text { fill: var(--ink); font-size: 11px; pointer-events: none; }
.node .dot { stroke: none; }
.edge { fill: none; stroke: var(--muted); stroke-width: 1.4; cursor: pointer; }
.edge:hover { stroke: var(--accent); stroke-width: 2.2; }
.edge-inferred { stroke-dasharray: 5 4; opacity: 0.8; }
.edge-label { fill: var(--muted); font-size: 9px; }
.legend { fill: var(--muted); font-size: 10px; }
.hub { fill: var(--bg); stroke: var(--line); stroke-dasharray: 3 3; }
```

- [ ] **Step 3: Append the graph and drawer code to `orchestra/static/app.js`**

```javascript
// ----------------------------------------------------------------- graph

const NODE_W = 170;
const NODE_H = 40;
const COL_GAP = 90;
const ROW_GAP = 18;
const EXACT_KINDS = ["spawn", "artifact", "message"];

function layoutGraph(run) {
  const nodes = [{ id: "main", label: "orchestrator", status: "completed",
                   sub: "this session", isMain: true }];
  for (const agent of run.agents) {
    nodes.push({
      id: agent.agent_id,
      label: agent.description || agent.agent_id,
      sub: agent.agent_type + " · " + (agent.model || "?"),
      status: agent.status,
      startedAt: agent.started_at,
    });
  }
  const byId = {};
  nodes.forEach((n) => { byId[n.id] = n; });
  const edges = (run.edges || []).filter((e) => byId[e.src] && byId[e.dst]);

  // Rank: longest path over exact edges. An inferred edge never sets a rank,
  // so a bad guess cannot rearrange the whole picture.
  const rank = {};
  nodes.forEach((n) => { rank[n.id] = 0; });
  const structural = edges.filter((e) => EXACT_KINDS.includes(e.kind));
  for (let pass = 0; pass < nodes.length; pass++) {
    let moved = false;
    for (const edge of structural) {
      const want = rank[edge.src] + 1;
      if (rank[edge.dst] < want) { rank[edge.dst] = want; moved = true; }
    }
    if (!moved) break;  // also the cycle guard: bounded by node count
  }

  const columns = {};
  for (const node of nodes) {
    node.rank = rank[node.id];
    (columns[node.rank] = columns[node.rank] || []).push(node);
  }
  for (const key in columns) {
    columns[key].sort((a, b) => (a.startedAt || 0) - (b.startedAt || 0));
  }

  // Two barycenter sweeps: cheap, and enough for the fan-out shapes real
  // orchestrations produce.
  const ranks = Object.keys(columns).map(Number).sort((a, b) => a - b);
  for (let sweep = 0; sweep < 2; sweep++) {
    for (const r of ranks) {
      const index = {};
      (columns[r - 1] || []).forEach((n, i) => { index[n.id] = i; });
      for (const node of columns[r]) {
        const parents = structural
          .filter((e) => e.dst === node.id && index[e.src] !== undefined)
          .map((e) => index[e.src]);
        node.bary = parents.length
          ? parents.reduce((a, b) => a + b, 0) / parents.length
          : Number.MAX_SAFE_INTEGER;
      }
      columns[r].sort((a, b) => (a.bary - b.bary) || 0);
    }
  }

  for (const r of ranks) {
    columns[r].forEach((node, i) => {
      node.x = 20 + r * (NODE_W + COL_GAP);
      node.y = 20 + i * (NODE_H + ROW_GAP);
    });
  }
  const width = 40 + (ranks.length) * (NODE_W + COL_GAP);
  const height = 40 + Math.max(...ranks.map((r) => columns[r].length)) *
    (NODE_H + ROW_GAP);
  return { nodes, edges, byId, width, height };
}

function renderGraph(run) {
  const svg = $("graph");
  svg.innerHTML = "";
  $("edge-evidence").hidden = true;
  const layout = layoutGraph(run);
  const width = Math.max(svg.clientWidth || 900, layout.width);
  svg.setAttribute("height", Math.max(layout.height, 200));
  svg.setAttribute("viewBox", "0 0 " + width + " " + Math.max(layout.height, 200));

  for (const edge of layout.edges) {
    const a = layout.byId[edge.src];
    const b = layout.byId[edge.dst];
    const x1 = a.x + NODE_W;
    const y1 = a.y + NODE_H / 2;
    const x2 = b.x;
    const y2 = b.y + NODE_H / 2;
    const mid = (x1 + x2) / 2;
    const path = svgEl("path", {
      d: "M" + x1 + "," + y1 + " C" + mid + "," + y1 + " " + mid + "," + y2 +
         " " + x2 + "," + y2,
      class: "edge" + (edge.confidence === "inferred" ? " edge-inferred" : ""),
    });
    path.appendChild(svgEl("title", {}, edge.kind + " (" + edge.confidence + ")"));
    path.onclick = () => showEvidence(edge);
    svg.appendChild(path);
  }

  for (const node of layout.nodes) {
    const group = svgEl("g", { class: "node" });
    group.appendChild(svgEl("rect", {
      x: node.x, y: node.y, width: NODE_W, height: NODE_H, rx: 6,
    }));
    group.appendChild(svgEl("circle", {
      cx: node.x + 12, cy: node.y + 14, r: 5,
      class: "dot s-" + node.status,
    }));
    const label = node.label.length > 22 ? node.label.slice(0, 21) + "…" : node.label;
    group.appendChild(svgEl("text", { x: node.x + 24, y: node.y + 18 }, label));
    group.appendChild(svgEl("text", {
      x: node.x + 24, y: node.y + 31, class: "edge-label",
    }, node.status + " · " + node.sub));
    if (!node.isMain) group.onclick = () => openDrawer(node.id);
    svg.appendChild(group);
  }

  svg.appendChild(svgEl("text", { x: 20, y: Math.max(layout.height, 200) - 8,
    class: "legend" }, "solid = exact  ·  dashed = inferred (click an edge for evidence)"));

  // Hub files are context, not dependencies, so they are listed rather than
  // drawn. They go to the footer, not #edge-evidence, which showEvidence()
  // overwrites wholesale.
  const hubs = (run.hub_files || []).map(
    (hub) => "shared context: " + hub.path + " (read by " +
             hub.reader_ids.length + " agents)");
  if (hubs.length) {
    // Appended, not assigned: renderDiagnostics ran first and may have put a
    // parse warning there that must not be thrown away.
    const footer = $("diagnostics");
    footer.textContent = [footer.textContent, hubs.join(" · ")]
      .filter(Boolean).join("  |  ");
  }
}

function showEvidence(edge) {
  const box = $("edge-evidence");
  box.hidden = false;
  const bits = ["<strong>" + edge.kind + "</strong> — " + edge.confidence,
                edge.src + " → " + edge.dst];
  const e = edge.evidence || {};
  if (e.path) bits.push("file: <code>" + e.path + "</code>");
  if (e.score !== undefined) {
    bits.push("overlap score: " + e.score + " · longest match: " +
              e.run_words + " words");
  }
  if (e.snippet) bits.push("<pre>" + e.snippet.replace(/[<>&]/g, "") + "</pre>");
  if (e.handoff) {
    bits.push("<em>result text also reused:</em> " + e.handoff.run_words +
              " word match");
  }
  box.innerHTML = bits.join("<br>");
}

// ---------------------------------------------------------------- drawer

function esc(text) {
  const div = document.createElement("div");
  div.textContent = text === null || text === undefined ? "" : String(text);
  return div.innerHTML;
}

async function openDrawer(agentId) {
  const drawer = $("drawer");
  drawer.hidden = false;
  drawer.innerHTML = "<p>Loading…</p>";
  let agent;
  try {
    agent = await api("/api/agent/" + encodeURIComponent(agentId));
  } catch (err) {
    drawer.innerHTML = "<p>Could not load this agent.</p>";
    return;
  }
  state.selected = agentId;

  const rows = [
    ["status", agent.status],
    ["type", agent.agent_type],
    ["model", agent.model],
    ["launch", agent.launch_mode],
    ["duration", fmtDuration(agent.duration_s)],
    ["tokens", fmtTokens(agent.tokens)],
    ["rounds", agent.rounds.length],
    ["tool calls", agent.tool_calls.length],
  ];

  const tools = agent.tool_calls.slice(-40)
    .map((t) => esc(t.name) + "  " + esc(t.target)).join("\n");

  drawer.innerHTML =
    '<button class="close" type="button" id="drawer-close">Close</button>' +
    "<h2>" + esc(agent.description || agent.agent_id) + "</h2>" +
    '<div class="source-note">' + esc(agent.agent_id) + "</div>" +
    "<dl>" + rows.map(([k, v]) =>
      "<dt>" + esc(k) + "</dt><dd>" + esc(v) + "</dd>").join("") + "</dl>" +
    "<h3>Objective</h3><pre>" + esc(agent.objective || "—") + "</pre>" +
    '<div class="source-note">' + esc(agent.objective_source) + "</div>" +
    "<h3>Expected output</h3><pre>" + esc(agent.expected_output || "—") + "</pre>" +
    '<div class="source-note">' + esc(agent.expected_output_source) + "</div>" +
    "<h3>Returned result</h3><pre>" + esc(agent.result || "(still running)") + "</pre>" +
    "<details><summary>Full brief</summary><pre>" + esc(agent.brief) + "</pre></details>" +
    "<details><summary>Tool calls (last 40)</summary><pre>" + tools + "</pre></details>" +
    "<h3>Files written</h3><pre>" + esc(agent.files_written.join("\n") || "—") + "</pre>" +
    "<h3>Files read</h3><pre>" + esc(agent.files_read.join("\n") || "—") + "</pre>";

  $("drawer-close").onclick = () => { drawer.hidden = true; state.selected = null; };
}

document.addEventListener("keydown", (event) => {
  if (event.key === "Escape") $("drawer").hidden = true;
});
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `python -m unittest tests.test_static_assets -v`
Expected: PASS, 10 tests.

- [ ] **Step 5: Verify the graph visually (manual)**

Start the server as in Task 12 Step 7 and open the Graph tab on a session that ran several agents.

Expected: the orchestrator sits at the far left with agents fanning right; an agent that read a file another wrote sits one column further right; solid curves are exact edges and dashed ones are inferred; clicking any edge prints its evidence below the graph; clicking a node opens the drawer showing the brief, the extracted expected output *with its extraction source underneath it*, and the returned result. Press Escape to close.

If every node lands in one column, the artifact edges are empty — go back to `normalize_path` rather than adjusting the layout.

- [ ] **Step 6: Commit**

```bash
git add orchestra/static/app.js orchestra/static/style.css tests/test_static_assets.py
git commit -m "feat: add evidence-backed handoff graph and agent drawer"
```

---

## Task 14: Self-contained HTML report

**Files:**
- Create: `orchestra/report.py`
- Test: `tests/test_report.py`

**Interfaces:**
- Consumes: `Run` (Task 1), `RunBuilder` (Task 10), the static assets (Tasks 12–13).
- Produces: `render_report(run, details) -> str`; `write_report(builder, path) -> str`.

The report inlines the CSS and JS and bakes the run data into the page as a JSON literal, so the file works with no server, no network, and no `k=` token.

- [ ] **Step 1: Write the failing test**

Create `tests/test_report.py`:

```python
import json
import os
import re
import tempfile
import unittest

from orchestra.build import RunBuilder
from orchestra.parent import parse_timestamp
from orchestra.report import render_report, write_report
from tests.fixtures import build_session, ts


class ReportTestCase(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp()
        self.paths = build_session(self.root)
        self.builder = RunBuilder(self.paths, now_fn=lambda: parse_timestamp(ts(150)))


class TestRenderReport(ReportTestCase):
    def test_contains_no_external_references(self):
        html = render_report(self.builder.refresh(), {})
        self.assertIsNone(re.search(r"""(?:src|href)=["'](?:https?:)?//""", html))

    def test_has_no_link_or_script_src_tags(self):
        html = render_report(self.builder.refresh(), {})
        self.assertNotIn('<link rel="stylesheet"', html)
        self.assertNotIn("<script src=", html)

    def test_embeds_the_run_payload(self):
        run = self.builder.refresh()
        html = render_report(run, {})
        match = re.search(r"window\.ORCHESTRA_RUN\s*=\s*(\{.*?\});", html, re.DOTALL)
        self.assertIsNotNone(match)
        payload = json.loads(match.group(1))
        self.assertEqual(payload["totals"]["agents"], 3)

    def test_embeds_agent_details(self):
        run = self.builder.refresh()
        details = {a.agent_id: a.to_detail_dict() for a in run.agents}
        html = render_report(run, details)
        match = re.search(r"window\.ORCHESTRA_DETAILS\s*=\s*(\{.*?\});", html, re.DOTALL)
        payload = json.loads(match.group(1))
        self.assertIn("You are planning", payload["a1"]["brief"])

    def test_title_names_the_session(self):
        self.assertIn("s1", render_report(self.builder.refresh(), {}))


class TestWriteReport(ReportTestCase):
    def test_writes_the_file_and_returns_its_path(self):
        target = os.path.join(self.root, "out", "report.html")
        written = write_report(self.builder, target)
        self.assertEqual(written, target)
        self.assertTrue(os.path.isfile(target))
        with open(target, encoding="utf-8") as fh:
            self.assertIn("ORCHESTRA_RUN", fh.read())

    def test_default_name_includes_the_session_id(self):
        written = write_report(self.builder, os.path.join(self.root, ""))
        self.assertIn("s1", os.path.basename(written))


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `python -m unittest tests.test_report -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'orchestra.report'`

- [ ] **Step 3: Write `orchestra/report.py`**

```python
"""A single-file HTML snapshot: no server, no network, safe to email."""

import json
import os
from typing import Any, Dict

from orchestra.build import RunBuilder
from orchestra.http import STATIC_DIR
from orchestra.model import Run

_SHELL = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Orchestra report — {session}</title>
<style>
{css}
</style>
</head>
<body>
<header>
  <div class="bar">
    <h1>Orchestra</h1>
    <span class="conn">static report · session {session}</span>
  </div>
  <div id="totals" class="totals"></div>
</header>
<div id="health" class="health" hidden></div>
<nav class="tabs" role="tablist">
  <button type="button" class="tab active" data-view="timeline" role="tab">Timeline</button>
  <button type="button" class="tab" data-view="graph" role="tab">Graph</button>
</nav>
<main>
  <section id="view-timeline" class="view">
    <svg id="timeline" role="img" aria-label="Agent timeline"></svg>
  </section>
  <section id="view-graph" class="view" hidden>
    <svg id="graph" role="img" aria-label="Agent dependency graph"></svg>
    <div id="edge-evidence" class="evidence" hidden></div>
  </section>
</main>
<aside id="drawer" class="drawer" hidden aria-label="Agent detail"></aside>
<footer id="diagnostics" class="diagnostics"></footer>
<select id="session-picker" hidden></select>
<button id="live-toggle" hidden></button>
<script>
window.ORCHESTRA_RUN = {run_json};
window.ORCHESTRA_DETAILS = {details_json};
</script>
<script>
{js}
</script>
</body>
</html>
"""


def _read_static(name: str) -> str:
    with open(os.path.join(STATIC_DIR, name), encoding="utf-8") as fh:
        return fh.read()


def _offline_shim(js: str) -> str:
    """Serve the baked-in payload instead of polling, and never schedule a poll."""
    shim = """
api = function (path) {
  if (path.indexOf("/api/agent/") === 0) {
    var id = decodeURIComponent(path.slice("/api/agent/".length));
    var detail = window.ORCHESTRA_DETAILS[id];
    return detail ? Promise.resolve(detail) : Promise.reject(new Error("no detail"));
  }
  if (path.indexOf("/api/run") === 0) return Promise.resolve(window.ORCHESTRA_RUN);
  return Promise.reject(new Error("offline"));
};
state.live = false;
"""
    # `api` and `state` are declared with const/let in app.js; rebind via window
    # after the definitions rather than before them.
    return js.replace("const TOKEN =", "var TOKEN =") \
             .replace("function api(path) {", "var api = function (path) {") \
             .replace("\n// ---------------------------------------------------------------- header",
                      "\n" + shim +
                      "\n// ---------------------------------------------------------------- header")


def render_report(run: Run, details: Dict[str, Dict[str, Any]]) -> str:
    return _SHELL.format(
        session=run.session_id,
        css=_read_static("style.css"),
        js=_offline_shim(_read_static("app.js")),
        run_json=json.dumps(run.to_summary_dict()),
        details_json=json.dumps(details),
    )


def write_report(builder: RunBuilder, path: str) -> str:
    """Render and write. An empty or directory path gets a session-named default."""
    run = builder.refresh()
    details = {a.agent_id: a.to_detail_dict() for a in run.agents}
    if not path or os.path.isdir(path) or path.endswith((os.sep, "/")):
        directory = path or os.getcwd()
        path = os.path.join(directory,
                            "orchestra-report-{}.html".format(run.session_id))
    parent = os.path.dirname(os.path.abspath(path))
    if parent:
        os.makedirs(parent, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(render_report(run, details))
    return path
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `python -m unittest tests.test_report -v`
Expected: PASS, 7 tests.

- [ ] **Step 5: Verify the report opens with no server (manual)**

```bash
python -c "import os;from orchestra.locate import find_session;from orchestra.build import RunBuilder;from orchestra.report import write_report;p=find_session(os.environ['CLAUDE_CODE_SESSION_ID']);print(write_report(RunBuilder(p), '.'))"
```

Open the printed file directly from disk (not through the server). Expected: timeline and graph render, the drawer opens from a baked-in payload, and the browser's network tab shows zero requests.

- [ ] **Step 6: Commit**

```bash
git add orchestra/report.py tests/test_report.py
git commit -m "feat: render self-contained offline HTML report"
```

---

## Task 15: CLI, portfile, and detached launch

**Files:**
- Create: `orchestra/__main__.py`
- Test: `tests/test_cli.py`

**Interfaces:**
- Consumes: everything above.
- Produces: `main(argv) -> int`; `portfile_path(session_id) -> str`; `write_portfile(session_id, port, token, pid)`; `read_portfile(session_id) -> Optional[dict]`; `server_is_alive(info) -> bool`; `url_for(info) -> str`.

CLI surface:

| Flags | Behaviour |
|---|---|
| (none) | Start if needed, print the tokenised URL, open a browser |
| `--stop` | Terminate this session's server and remove its portfile |
| `--report [PATH]` | Write an offline HTML snapshot; no server needed |
| `--serve` | Internal: run the server in the foreground (what the detached child runs) |
| `--session ID` `--port N` `--no-open` | Modifiers |

**Why a portfile and not a fixed port:** two Claude Code sessions can run at once, and each needs its own dashboard. Keying the portfile by session id makes `/orchestra` idempotent per session and lets a second session start its own server without a collision.

- [ ] **Step 1: Write the failing test**

Create `tests/test_cli.py`:

```python
import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stdout

from orchestra.__main__ import (main, portfile_path, read_portfile, url_for,
                                write_portfile)
from tests.fixtures import build_session


class TestPortfile(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        os.environ["ORCHESTRA_STATE_DIR"] = self.tmp
        self.addCleanup(os.environ.pop, "ORCHESTRA_STATE_DIR", None)

    def test_round_trip(self):
        write_portfile("s1", 7717, "tok", 4242)
        info = read_portfile("s1")
        self.assertEqual(info["port"], 7717)
        self.assertEqual(info["token"], "tok")
        self.assertEqual(info["pid"], 4242)

    def test_missing_portfile_is_none(self):
        self.assertIsNone(read_portfile("nope"))

    def test_corrupt_portfile_is_none(self):
        with open(portfile_path("s2"), "w") as fh:
            fh.write("{not json")
        self.assertIsNone(read_portfile("s2"))

    def test_path_is_keyed_by_session(self):
        self.assertNotEqual(portfile_path("a"), portfile_path("b"))
        self.assertTrue(portfile_path("a").startswith(self.tmp))

    def test_url_contains_loopback_port_and_token(self):
        url = url_for({"port": 7717, "token": "abc", "session": "s1"})
        self.assertTrue(url.startswith("http://127.0.0.1:7717/"))
        self.assertIn("k=abc", url)
        self.assertIn("session=s1", url)


class TestReportCommand(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp()
        build_session(self.root, "s1")
        os.environ["CLAUDE_CONFIG_DIR"] = self.root
        os.environ["ORCHESTRA_STATE_DIR"] = tempfile.mkdtemp()
        self.addCleanup(os.environ.pop, "CLAUDE_CONFIG_DIR", None)
        self.addCleanup(os.environ.pop, "ORCHESTRA_STATE_DIR", None)

    def test_report_writes_a_file_and_prints_its_path(self):
        target = os.path.join(self.root, "r.html")
        out = io.StringIO()
        with redirect_stdout(out):
            code = main(["--session", "s1", "--report", target])
        self.assertEqual(code, 0)
        self.assertTrue(os.path.isfile(target))
        self.assertIn(target, out.getvalue())

    def test_unknown_session_exits_nonzero_with_a_clear_message(self):
        out = io.StringIO()
        with redirect_stdout(out):
            code = main(["--session", "missing", "--report", "x.html"])
        self.assertEqual(code, 2)
        self.assertIn("missing", out.getvalue())


class TestStopCommand(unittest.TestCase):
    def setUp(self):
        os.environ["ORCHESTRA_STATE_DIR"] = tempfile.mkdtemp()
        self.addCleanup(os.environ.pop, "ORCHESTRA_STATE_DIR", None)

    def test_stop_without_a_server_is_not_an_error(self):
        out = io.StringIO()
        with redirect_stdout(out):
            code = main(["--session", "s1", "--stop"])
        self.assertEqual(code, 0)
        self.assertIn("not running", out.getvalue().lower())

    def test_stop_removes_a_stale_portfile(self):
        write_portfile("s1", 7717, "tok", 999999)  # pid that does not exist
        out = io.StringIO()
        with redirect_stdout(out):
            main(["--session", "s1", "--stop"])
        self.assertIsNone(read_portfile("s1"))


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `python -m unittest tests.test_cli -v`
Expected: FAIL — `ImportError: cannot import name 'main' from 'orchestra.__main__'`

- [ ] **Step 3: Write `orchestra/__main__.py`**

```python
"""The /orchestra command line."""

import argparse
import json
import os
import secrets
import signal
import subprocess
import sys
import tempfile
import time
import webbrowser
from typing import Any, Dict, List, Optional

from orchestra import constants as C

MIN_PYTHON = (3, 9)


def _state_dir() -> str:
    directory = os.environ.get("ORCHESTRA_STATE_DIR") or \
        os.path.join(tempfile.gettempdir(), "orchestra")
    os.makedirs(directory, exist_ok=True)
    return directory


def portfile_path(session_id: str) -> str:
    safe = "".join(ch for ch in session_id if ch.isalnum() or ch in "-_")
    return os.path.join(_state_dir(), "{}.json".format(safe or "default"))


def logfile_path(session_id: str) -> str:
    return portfile_path(session_id)[: -len(".json")] + ".log"


def write_portfile(session_id: str, port: int, token: str, pid: int) -> None:
    with open(portfile_path(session_id), "w", encoding="utf-8") as fh:
        json.dump({"port": port, "token": token, "pid": pid,
                   "session": session_id}, fh)


def read_portfile(session_id: str) -> Optional[Dict[str, Any]]:
    try:
        with open(portfile_path(session_id), encoding="utf-8") as fh:
            info = json.load(fh)
    except (OSError, ValueError):
        return None
    return info if isinstance(info, dict) and "port" in info else None


def remove_portfile(session_id: str) -> None:
    try:
        os.remove(portfile_path(session_id))
    except OSError:
        pass


def url_for(info: Dict[str, Any]) -> str:
    return "http://127.0.0.1:{}/?k={}&session={}".format(
        info["port"], info.get("token", ""), info.get("session", ""))


def server_is_alive(info: Dict[str, Any]) -> bool:
    import urllib.error
    import urllib.request
    try:
        with urllib.request.urlopen(
                "http://127.0.0.1:{}/api/health".format(info["port"]), timeout=1):
            return True
    except (urllib.error.URLError, OSError):
        return False


def _kill(pid: int) -> None:
    try:
        if os.name == "nt":
            subprocess.run(["taskkill", "/PID", str(pid), "/F"],
                           capture_output=True, check=False)
        else:
            os.kill(pid, signal.SIGTERM)
    except (OSError, ValueError):
        pass


# -- commands -------------------------------------------------------------

def _resolve_session(args) -> str:
    return args.session or os.environ.get("CLAUDE_CODE_SESSION_ID", "")


def cmd_serve(args) -> int:
    """Foreground server. This is what the detached child process runs."""
    from orchestra.http import make_server, start_idle_watchdog
    from orchestra.service import OrchestraService

    session_id = _resolve_session(args)
    token = args.token or secrets.token_urlsafe(24)
    service = OrchestraService(token=token, default_session=session_id)

    port = args.port
    server = None
    for attempt in range(20):
        try:
            server = make_server(service, port if port else 0)
            break
        except OSError:
            port = (port or C.DEFAULT_PORT) + 1
    if server is None:
        print("could not bind a port", file=sys.stderr)
        return 1

    write_portfile(session_id, server.server_port, token, os.getpid())
    start_idle_watchdog(server)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        remove_portfile(session_id)
    return 0


def cmd_start(args) -> int:
    session_id = _resolve_session(args)
    if not session_id:
        print("no session id: pass --session or run inside Claude Code")
        return 2

    info = read_portfile(session_id)
    if info and server_is_alive(info):
        print(url_for(info))
        if not args.no_open:
            webbrowser.open(url_for(info))
        return 0
    if info:
        remove_portfile(session_id)

    command = [sys.executable, "-m", "orchestra", "--serve",
               "--session", session_id]
    if args.port:
        command += ["--port", str(args.port)]

    log = open(logfile_path(session_id), "wb")
    kwargs: Dict[str, Any] = {"stdout": log, "stderr": log, "stdin": subprocess.DEVNULL}
    if os.name == "nt":
        kwargs["creationflags"] = (getattr(subprocess, "DETACHED_PROCESS", 0x00000008) |
                                   getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x200))
    else:
        kwargs["start_new_session"] = True
    subprocess.Popen(command, **kwargs)

    deadline = time.time() + 5.0
    while time.time() < deadline:
        info = read_portfile(session_id)
        if info and server_is_alive(info):
            print(url_for(info))
            if not args.no_open:
                webbrowser.open(url_for(info))
            return 0
        time.sleep(0.15)

    # A silent non-start is the worst failure for a visibility tool: say why.
    print("orchestra failed to start within 5s")
    try:
        with open(logfile_path(session_id), encoding="utf-8", errors="replace") as fh:
            tail = fh.read()[-2000:]
        if tail.strip():
            print(tail)
    except OSError:
        pass
    return 1


def cmd_stop(args) -> int:
    session_id = _resolve_session(args)
    info = read_portfile(session_id)
    if not info:
        print("orchestra is not running for this session")
        return 0
    _kill(int(info.get("pid", 0)))
    remove_portfile(session_id)
    print("orchestra stopped")
    return 0


def cmd_report(args) -> int:
    from orchestra.build import RunBuilder
    from orchestra.locate import find_session
    from orchestra.report import write_report

    session_id = _resolve_session(args)
    paths = find_session(session_id)
    if paths is None:
        print("no transcript found for session: {}".format(session_id or "(none)"))
        return 2
    written = write_report(RunBuilder(paths), args.report)
    print(written)
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    if sys.version_info < MIN_PYTHON:
        print("orchestra needs Python {}.{} or newer; this is {}.{}".format(
            MIN_PYTHON[0], MIN_PYTHON[1], sys.version_info[0], sys.version_info[1]))
        return 2

    parser = argparse.ArgumentParser(prog="orchestra", add_help=True)
    parser.add_argument("--session", default="")
    parser.add_argument("--port", type=int, default=0)
    parser.add_argument("--token", default="")
    parser.add_argument("--no-open", action="store_true")
    parser.add_argument("--serve", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--stop", action="store_true")
    parser.add_argument("--report", nargs="?", const="", default=None)
    args = parser.parse_args(argv)

    if args.serve:
        return cmd_serve(args)
    if args.stop:
        return cmd_stop(args)
    if args.report is not None:
        return cmd_report(args)
    return cmd_start(args)


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `python -m unittest tests.test_cli -v`
Expected: PASS, 9 tests.

- [ ] **Step 5: Verify the real start/stop cycle (manual)**

```bash
python -m orchestra --no-open
python -m orchestra --no-open   # second call must reuse, not start a twin
python -m orchestra --stop
```

Expected: the first call prints a URL within a second or two; the second prints the *same* URL; `--stop` prints "orchestra stopped" and the URL then refuses connections.

- [ ] **Step 6: Commit**

```bash
git add orchestra/__main__.py tests/test_cli.py
git commit -m "feat: add CLI with detached launch, portfile reuse, and stop"
```

---

## Task 16: Plugin packaging

**Files:**
- Create: `.claude-plugin/plugin.json`, `.claude-plugin/marketplace.json`, `commands/orchestra.md`, `README.md`, `LICENSE`
- Test: `tests/test_packaging.py`

**Interfaces:**
- Consumes: `python -m orchestra` (Task 15).
- Produces: the installable plugin and the `/orchestra` slash command.

- [ ] **Step 1: Write the failing test**

Create `tests/test_packaging.py`:

```python
import json
import os
import re
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def read(*parts):
    with open(os.path.join(ROOT, *parts), encoding="utf-8") as fh:
        return fh.read()


class TestPluginManifest(unittest.TestCase):
    def test_plugin_json_is_valid_and_named(self):
        data = json.loads(read(".claude-plugin", "plugin.json"))
        self.assertEqual(data["name"], "orchestra")
        self.assertIn("description", data)
        self.assertRegex(data["version"], r"^\d+\.\d+\.\d+$")

    def test_marketplace_lists_the_plugin(self):
        data = json.loads(read(".claude-plugin", "marketplace.json"))
        names = [p["name"] for p in data["plugins"]]
        self.assertIn("orchestra", names)


class TestSlashCommand(unittest.TestCase):
    def test_frontmatter_limits_tools_to_bash(self):
        text = read("commands", "orchestra.md")
        front = re.match(r"^---\n(.*?)\n---\n", text, re.DOTALL)
        self.assertIsNotNone(front, "command file needs YAML frontmatter")
        self.assertIn("allowed-tools: Bash", front.group(1))

    def test_documents_all_three_invocations(self):
        text = read("commands", "orchestra.md")
        for fragment in ("--stop", "--report", "CLAUDE_CODE_SESSION_ID"):
            self.assertIn(fragment, text)


class TestReadme(unittest.TestCase):
    def test_states_the_local_only_guarantee(self):
        text = read("README.md").lower()
        self.assertIn("127.0.0.1", text)
        self.assertIn("standard library", text)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `python -m unittest tests.test_packaging -v`
Expected: FAIL — `FileNotFoundError` on `.claude-plugin/plugin.json`.

- [ ] **Step 3: Write `.claude-plugin/plugin.json`**

```json
{
  "name": "orchestra",
  "version": "0.1.0",
  "description": "Local read-only dashboard for Claude Code subagent orchestration: fleet timeline, evidence-backed handoff graph, per-agent drill-down, token and health analytics.",
  "author": {"name": "Chandan"},
  "keywords": ["subagents", "observability", "dashboard", "orchestration"]
}
```

- [ ] **Step 4: Write `.claude-plugin/marketplace.json`**

```json
{
  "name": "orchestra-marketplace",
  "owner": {"name": "Chandan"},
  "plugins": [
    {
      "name": "orchestra",
      "source": "./",
      "description": "See what your subagents are actually doing."
    }
  ]
}
```

- [ ] **Step 5: Write `commands/orchestra.md`**

````markdown
---
description: Open the Orchestra dashboard for this session's subagents
allowed-tools: Bash
argument-hint: "[stop | report [path]]"
---

Run Orchestra for the current Claude Code session.

The plugin directory is `${CLAUDE_PLUGIN_ROOT}`. Run every command from there so
`python -m orchestra` resolves.

Dispatch on `$ARGUMENTS`:

- **empty** — start the dashboard and print its URL:
  ```bash
  cd "${CLAUDE_PLUGIN_ROOT}" && python -m orchestra --session "$CLAUDE_CODE_SESSION_ID"
  ```
- **`stop`** — shut the server down:
  ```bash
  cd "${CLAUDE_PLUGIN_ROOT}" && python -m orchestra --session "$CLAUDE_CODE_SESSION_ID" --stop
  ```
- **`report`** or **`report <path>`** — write a self-contained HTML snapshot:
  ```bash
  cd "${CLAUDE_PLUGIN_ROOT}" && python -m orchestra --session "$CLAUDE_CODE_SESSION_ID" --report "<path or .>"
  ```

Report back to the user exactly what the command printed — the URL, the report
path, or the failure text. Do not paraphrase a failure as success, and do not
retry a failed start more than once.

If the command prints "no session id", tell the user Orchestra could not detect
the session and they can pass one explicitly with `--session`.
````

- [ ] **Step 6: Write `README.md`**

````markdown
# Orchestra

See what your Claude Code subagents are actually doing.

Orchestra reads the transcripts Claude Code already writes and reconstructs the
whole orchestration: how many agents ran, what each was asked to do, what each
was expected to produce, which are still going, which are stuck, and which
agent's output became which other agent's input.

## Install

```bash
/plugin marketplace add <this repo>
/plugin install orchestra
```

Requires Python 3.9 or newer. Nothing else — no pip install, no npm, no build.

## Use

| Command | What it does |
|---|---|
| `/orchestra` | Start the dashboard and open it |
| `/orchestra stop` | Shut the server down |
| `/orchestra report` | Write a self-contained HTML snapshot you can share |

## What you get

- **Timeline** — one row per agent, with parallel waves banded together.
- **Graph** — who fed whom. Solid edges are exact (spawn, file handoff, direct
  message); dashed edges are inferred from text reuse and carry the snippet that
  produced them, so you can judge them yourself.
- **Drawer** — each agent's brief, its extracted objective and expected output,
  its returned result, its tool calls, and the files it read and wrote.
- **Health** — stalled, failed, and orphaned agents surfaced instead of buried.

## Privacy

Orchestra is local and read-only.

- The server binds `127.0.0.1` only, and every API call requires a token minted
  at launch.
- There is no network egress of any kind: no CDN, no fonts, no telemetry. Every
  asset ships in the package.
- It never writes to any file under `~/.claude`.
- Anything that looks like a credential is redacted before it reaches the page.

## Development

```bash
python -m unittest discover -s tests -t . -v
```

Standard library only, tests included.
````

- [ ] **Step 7: Write `LICENSE`**

Use the MIT license text with the current year and `Chandan` as the copyright holder.

- [ ] **Step 8: Run the full suite**

Run: `python -m unittest discover -s tests -t . -v`
Expected: PASS, every test across all modules.

- [ ] **Step 9: Verify the slash command end to end (manual)**

Install the plugin locally, then in a Claude Code session that has run subagents, type `/orchestra`. Expected: a URL is printed and the browser opens on your real fleet. Then `/orchestra report` and confirm the written file opens offline.

- [ ] **Step 10: Commit**

```bash
git add .claude-plugin/ commands/ README.md LICENSE tests/test_packaging.py
git commit -m "feat: package Orchestra as an installable Claude Code plugin"
```
