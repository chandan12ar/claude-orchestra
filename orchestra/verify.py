"""Did an agent check its work after its last code edit?

Rule-based and explainable, like the loop detector: the evidence is the agent's own
tool calls, which the reader can open. An agent that edited code and then ran a test,
build, type check or lint is "checked" (or "failing" when that last check failed); one
that ran nothing of the kind after its last edit is "unchecked". That says what was
*seen*, never that the work is wrong: an agent may verify by running a one-off script,
which no rule can tell apart from any other command.

Both decisions are made on the full tool input at read time (ToolCall.verify), because
the stored target is cut at 120 characters and a long path loses its extension.
"""

import os
import re
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence

from orchestra.redact import scrub

CHECKED = "checked"
FAILING = "failing"
UNCHECKED = "unchecked"

EDIT = "edit"        # ToolCall.verify: a write to a code or config file
CHECK = "check"      # ToolCall.verify: a test, build, type check or lint command
RUN = "run"          # ToolCall.verify: a command that runs a script file (ToolCall.ref names it)

_CODE_EXT = frozenset((
    "py", "pyi", "ipynb", "js", "mjs", "cjs", "jsx", "ts", "tsx", "mts", "cts", "vue", "svelte",
    "go", "rs", "java", "kt", "kts", "scala", "cs", "fs", "c", "cc", "cpp", "cxx", "h", "hpp",
    "m", "mm", "swift", "rb", "php", "pl", "lua", "r", "dart", "ex", "exs", "erl", "hs", "clj",
    "sql", "sh", "bash", "zsh", "ps1", "psm1", "bat", "cmd",
    "css", "scss", "sass", "less", "html", "htm",
    "json", "yaml", "yml", "toml", "ini", "cfg", "xml", "gradle", "proto", "graphql", "tf"))
_CODE_NAMES = frozenset(("makefile", "dockerfile", "rakefile", "gemfile", "justfile"))

# Commands that check work, across the common ecosystems. Matched anywhere in the
# command, so "cd app && npm test" counts.
_BUILTIN = (
    r"\bpytest\b|\bpython[\d.]*\s+-m\s+(pytest|unittest|mypy|pyright|ruff|flake8|pylint|tox|nox)\b"
    r"|\b(mypy|pyright|ruff|flake8|pylint|tox|nox|black\s+--check)\b"
    r"|\b(npm|pnpm|yarn|bun)\s+(run\s+)?[\w:.-]*(test|lint|build|typecheck|type-check|check|verify)\b"
    r"|\b(npx\s+)?(tsc|jest|vitest|mocha|eslint|biome|playwright\s+test|cypress\s+run)\b"
    r"|\bnode\s+--test\b|\bdeno\s+(test|check|lint)\b"
    r"|\bcargo\s+(test|check|build|clippy|nextest)\b|\bgo\s+(test|vet|build)\b|\bgolangci-lint\b"
    r"|\b(mvn|mvnw|gradle|gradlew)\b|\bdotnet\s+(test|build)\b|\bswift\s+(test|build)\b"
    r"|\bmake\s+([\w-]*test|check|lint|build)\b|\bctest\b|\bbazel\s+(test|build)\b"
    r"|\b(rspec|rubocop|phpunit|phpstan|mix\s+test|stack\s+test|cabal\s+test|flutter\s+test|dart\s+test)\b"
    r"|\bclaude\s+plugin\s+validate\b")
# A name only counts standing alone: not "vitest.config.ts", not ".pytest_cache".
_BUILTIN_RE = re.compile(r"(?<![\w.\-])(?:" + _BUILTIN + r")(?![\w.\-])", re.IGNORECASE)
# A here-document's body is data (a script, a commit message), not the command line.
_HEREDOC_RE = re.compile(r"<<-?\s*(['\"]?)(\w+)\1([^\n]*)\n.*?\n\s*\2(?=\s|$)", re.DOTALL)


def command_line(command: str) -> str:
    """The command without here-document bodies: "python - <<'EOF' ... EOF && pytest" keeps "&& pytest"."""
    return _HEREDOC_RE.sub(lambda m: "<<" + m.group(2) + m.group(3), command)


def _custom_pattern() -> "tuple[Optional[re.Pattern], str]":
    raw = os.environ.get("ORCHESTRA_VERIFY_PATTERN", "").strip()
    if not raw:
        return None, ""
    try:
        return re.compile(raw, re.IGNORECASE), ""
    except re.error as exc:
        return None, "ORCHESTRA_VERIFY_PATTERN is not a valid regular expression ({}); it was ignored.".format(exc)


CUSTOM_RE, PATTERN_ERROR = _custom_pattern()

# Throwaway files: a scratch or temp folder is not the project, so editing a script there
# and running it is not an unverified change.
_SCRATCH_RE = re.compile(r"(^|[\\/])(scratchpad|scratch|tmp|temp)[\\/]", re.IGNORECASE)
# An interpreter, then any code file named after it: running the file just edited.
_INTERPRETER_RE = re.compile(
    r"(?<![\w.\-])(?:python[\d.]*|py|node|deno\s+run|bun|tsx|ts-node|bash|sh|zsh|pwsh|powershell(?:\.exe)?|"
    r"ruby|php|perl|Rscript|go\s+run)\s", re.IGNORECASE)
_TOKEN_RE = re.compile(r"[^\s\"'|&;<>()]+")

_CHECK_TOOLS = ("Bash", "PowerShell")
_EDIT_FIELDS = {"Write": "file_path", "Edit": "file_path", "MultiEdit": "file_path",
                "NotebookEdit": "notebook_path"}


def is_code(path: str) -> bool:
    name = re.split(r"[\\/]", path.strip())[-1].lower()
    if name in _CODE_NAMES:
        return True
    return "." in name and name.rsplit(".", 1)[1] in _CODE_EXT


def is_scratch(path: str) -> bool:
    return bool(_SCRATCH_RE.search(path))


def basename(path: str) -> str:
    return re.split(r"[\\/]", path.strip().strip("\"'"))[-1].lower()


def is_check(command: str) -> bool:
    return bool(_BUILTIN_RE.search(command) or (CUSTOM_RE is not None and CUSTOM_RE.search(command)))


def classify(name: str, params: Dict[str, Any]) -> "tuple[str, str]":
    """What a tool call means here, and the file it names: (EDIT | CHECK | RUN | "", ref).

    ref is a lower-case file name: the file an EDIT wrote, or the scripts a RUN ran
    (newline-separated), so a run of the file just edited can count as checking it.
    """
    field_name = _EDIT_FIELDS.get(name)
    if field_name:
        path = params.get(field_name)
        if isinstance(path, str) and is_code(path) and not is_scratch(path):
            return EDIT, basename(path)
        return "", ""
    if name in _CHECK_TOOLS:
        command = params.get("command")
        if not isinstance(command, str):
            return "", ""
        command = command_line(command)
        if is_check(command):
            return CHECK, ""
        first = _INTERPRETER_RE.search(command)
        ran = ([basename(t) for t in _TOKEN_RE.findall(command[first.end():]) if is_code(t)]
               if first else [])
        if ran:
            return RUN, "\n".join(dict.fromkeys(ran))
    return "", ""


@dataclass
class Verification:
    state: str
    last_edit: Dict[str, Any]
    last_check: Optional[Dict[str, Any]] = None   # the last check after the last edit
    checked_before: bool = False                  # a check ran, but only before the last edit
    final: bool = False                           # the agent has finished; otherwise "so far"

    def to_dict(self) -> Dict[str, Any]:
        return {"state": self.state, "last_edit": self.last_edit, "last_check": self.last_check,
                "checked_before": self.checked_before, "final": self.final}


def _call(call: Any) -> Dict[str, Any]:
    return {"tool": call.name, "target": scrub(call.target), "at": call.timestamp}


def assess(tool_calls: Sequence[Any], final: bool) -> Optional[Verification]:
    """None when the agent edited no code; otherwise what followed its last code edit.

    A check still running (no result yet, ok is None) counts as run but not passed,
    so it reads "failing" only once it has actually failed.
    """
    calls = sorted((c for c in tool_calls if getattr(c, "verify", "")),
                   key=lambda c: c.timestamp if c.timestamp is not None else 0.0)
    edits = [i for i, c in enumerate(calls) if c.verify == EDIT]
    if not edits:
        return None
    last = edits[-1]
    edited = calls[last].ref
    # A check, or running the very file that was edited last.
    after = [c for c in calls[last + 1:]
             if c.verify == CHECK or (c.verify == RUN and edited and edited in c.ref.split("\n"))]
    before = any(c.verify == CHECK for c in calls[:last])
    v = Verification(state=UNCHECKED, last_edit=_call(calls[last]), checked_before=before, final=final)
    if after:
        check = after[-1]
        v.last_check = dict(_call(check), ok=check.ok)
        v.state = FAILING if check.ok is False else CHECKED
    return v


def summary(agents: List[Any]) -> Optional[Dict[str, Any]]:
    """Run-level counts and the agents worth a look, for the Insights card."""
    rows = []
    counts = {CHECKED: 0, FAILING: 0, UNCHECKED: 0}
    for agent in agents:
        v = agent.verification
        if not v:
            continue
        counts[v["state"]] += 1
        if v["state"] != CHECKED:
            rows.append({"agent_id": agent.agent_id, "label": scrub(agent.description)[:60],
                         "status": agent.status, **v})
    if not any(counts.values()):
        return {"edited": 0, "counts": counts, "attention": [], "pattern_error": PATTERN_ERROR}
    rows.sort(key=lambda r: (r["state"] != FAILING, not r["final"], r["label"]))
    return {"edited": sum(counts.values()), "counts": counts, "attention": rows[:12],
            "pattern_error": PATTERN_ERROR}
