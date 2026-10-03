"""What a run produced: commits, pushes, pull requests, merges and test runs.

Claude Code records git results on a Bash call's result (``toolUseResult.gitOperation``:
``commit`` with sha and branch, ``push`` with branch, ``pr`` with number, url and action,
``branch`` with ref and action) and writes ``pr-link`` entries when a session is linked
to a pull request. Those are facts, so nothing is parsed out of command text except a
commit's first message line, for display. Test runs reuse the check rules of
orchestra.verify, with the result's error flag saying whether they passed.

One log per transcript (each agent's and the main session's). A forked agent replays
its parent's history, so the run-level summary removes duplicates by sha and url.
"""

import re
from collections import OrderedDict
from typing import Any, Dict, List, Optional, Tuple

from orchestra.parent import content_blocks, parse_timestamp
from orchestra.redact import scrub
from orchestra.verify import CHECK, classify, command_line

MAX_ITEMS = 200            # commits, pull requests and merges kept per transcript
_SHELLS = ("Bash", "PowerShell")
_SAFE_URL = re.compile(r"^https://[^\s\"'<>]+$")
# A real commit on the command line (not --dry-run, not text inside a here-document).
_COMMIT_RE = re.compile(r"(?<![\w.\-])git\s+(?:-C\s+\S+\s+)?commit\b(?![^;&|\n]*--dry-run)")
# git commit -m "first line" / -m 'first line' / a here-document after the command.
_MESSAGE_RE = re.compile(r"""-m\s+(?:"((?:[^"\\]|\\.)*)"|'([^']*)')""")
_HEREDOC_BODY_RE = re.compile(r"<<-?\s*['\"]?(\w+)['\"]?[^\n]*\n(.*?)\n\s*\1(?=\s|$)", re.DOTALL)


def commit_message(command: str) -> str:
    """The first line of the message a git commit command gave, or ""."""
    at = command.find("git commit")
    if at < 0:
        return ""
    tail = command[at:]
    found = _MESSAGE_RE.search(tail)
    text = ""
    if found:
        text = found.group(1) if found.group(1) is not None else found.group(2)
        if text.lstrip().startswith("$(cat <<"):
            text = ""
    if not text:
        body = _HEREDOC_BODY_RE.search(tail)
        text = body.group(2) if body else ""
    for line in text.replace("\\n", "\n").split("\n"):
        if line.strip():
            return line.strip()[:120]
    return ""


def safe_url(url: Any) -> str:
    return url if isinstance(url, str) and _SAFE_URL.match(url) else ""


class OutcomeLog:
    """Incremental, like the digests: ingest() may be called with each new batch."""

    def __init__(self) -> None:
        self.commits: "OrderedDict[str, Dict[str, Any]]" = OrderedDict()
        self.prs: "OrderedDict[str, Dict[str, Any]]" = OrderedDict()
        self.merges: List[Dict[str, Any]] = []
        self.pushes = 0
        self.last_push: Optional[Dict[str, Any]] = None
        self.checks_passed = 0
        self.checks_failed = 0
        self._pending: Dict[str, Tuple[str, Dict[str, Any]]] = {}

    def ingest(self, entries: List[Dict[str, Any]]) -> None:
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            at = parse_timestamp(entry.get("timestamp"))
            if entry.get("type") == "pr-link":
                self._pr(entry.get("prNumber"), entry.get("prUrl"), entry.get("prRepository"), "linked", at)
                continue
            blocks = content_blocks(entry)
            results = [b for b in blocks if b.get("type") == "tool_result"]
            for block in blocks:
                if block.get("type") == "tool_use" and block.get("name") in _SHELLS:
                    params = block.get("input") if isinstance(block.get("input"), dict) else {}
                    self._pending[str(block.get("id", ""))] = (str(block.get("name")), params)
            for block in results:
                pending = self._pending.pop(str(block.get("tool_use_id", "")), None)
                if pending is None:
                    continue
                name, params = pending
                failed = bool(block.get("is_error"))
                if classify(name, params)[0] == CHECK:
                    if failed:
                        self.checks_failed += 1
                    else:
                        self.checks_passed += 1
                if failed:
                    continue
                record = entry.get("toolUseResult") if len(results) == 1 else None
                op = record.get("gitOperation") if isinstance(record, dict) else None
                command = params.get("command") if isinstance(params.get("command"), str) else ""
                self._git(op if isinstance(op, dict) else {}, at, command,
                          str(block.get("tool_use_id", "")))

    def _git(self, op: Dict[str, Any], at: Optional[float], command: str, use_id: str) -> None:
        commit = op.get("commit")
        if isinstance(commit, dict) and isinstance(commit.get("sha"), str):
            self._commit(commit["sha"], commit["sha"], str(commit.get("branch") or ""),
                         str(commit.get("kind") or ""), at, command)
        elif _COMMIT_RE.search(command_line(command)):
            # Claude Code reads the commit from git's output; "git commit -q" prints none, so
            # the sha is unknown. Keyed by the tool call: a forked agent replays the same id.
            push = op.get("push") if isinstance(op.get("push"), dict) else {}
            self._commit("tool:" + use_id, "", str(push.get("branch") or ""), "committed", at, command)
        self._other(op, at)

    def _commit(self, key: str, sha: str, branch: str, kind: str, at: Optional[float], command: str) -> None:
        if key in self.commits or len(self.commits) >= MAX_ITEMS:
            return
        self.commits[key] = {"key": key, "sha": sha, "branch": branch, "kind": kind, "at": at,
                             "message": commit_message(command)}

    def _other(self, op: Dict[str, Any], at: Optional[float]) -> None:
        push = op.get("push")
        if isinstance(push, dict):
            self.pushes += 1
            self.last_push = {"branch": str(push.get("branch") or ""), "at": at}
        pr = op.get("pr")
        if isinstance(pr, dict):
            self._pr(pr.get("number"), pr.get("url"), None, str(pr.get("action") or ""), at)
        branch = op.get("branch")
        if isinstance(branch, dict) and len(self.merges) < MAX_ITEMS:
            self.merges.append({"ref": str(branch.get("ref") or ""), "action": str(branch.get("action") or ""),
                                "at": at})

    def _pr(self, number: Any, url: Any, repo: Any, action: str, at: Optional[float]) -> None:
        url = safe_url(url)
        if not url:
            return
        known = self.prs.get(url)
        if known is not None:
            if action == "created":          # a created PR stays "created" however often it is linked
                known["action"] = action
            return
        if len(self.prs) >= MAX_ITEMS:
            return
        repo = repo if isinstance(repo, str) else (re.sub(r"^https://github\.com/([^/]+/[^/]+)/.*$", r"\1", url)
                                                   if url.startswith("https://github.com/") else "")
        self.prs[url] = {"number": number if isinstance(number, int) else None, "url": url,
                         "repo": repo, "action": action, "at": at}

    def empty(self) -> bool:
        return not (self.commits or self.prs or self.merges or self.pushes
                    or self.checks_passed or self.checks_failed)

    def snapshot(self) -> "OutcomeLog":
        copy = OutcomeLog()
        copy.commits = OrderedDict((k, dict(v)) for k, v in self.commits.items())
        copy.prs = OrderedDict((k, dict(v)) for k, v in self.prs.items())
        copy.merges = [dict(m) for m in self.merges]
        copy.pushes, copy.last_push = self.pushes, self.last_push
        copy.checks_passed, copy.checks_failed = self.checks_passed, self.checks_failed
        return copy

    def to_dict(self) -> Dict[str, Any]:
        return {"commits": [dict(c, message=scrub(c["message"]), branch=scrub(c["branch"]))
                            for c in self.commits.values()],
                "prs": [dict(p, repo=scrub(p["repo"]), url=scrub(p["url"])) for p in self.prs.values()],
                "merges": [dict(m, ref=scrub(m["ref"])) for m in self.merges],
                "pushes": self.pushes,
                "checks": {"passed": self.checks_passed, "failed": self.checks_failed}}


def summary(sources: List[Tuple[str, str, Optional["OutcomeLog"]]], cost: Optional[Dict[str, Any]],
            fresh_tokens: int) -> Optional[Dict[str, Any]]:
    """The run's outcomes, deduplicated by sha and url, attributed to the first transcript
    that recorded them. sources: (agent_id or "" for the main session, label, log)."""
    commits: "OrderedDict[str, Dict[str, Any]]" = OrderedDict()
    prs: "OrderedDict[str, Dict[str, Any]]" = OrderedDict()
    merges: List[Dict[str, Any]] = []
    pushes = passed = failed = 0
    for agent_id, label, log in sources:
        if log is None or log.empty():
            continue
        d = log.to_dict()
        who = {"agent_id": agent_id, "label": scrub(label)[:60] or ("Main session" if not agent_id else agent_id)}
        for c in d["commits"]:
            if c["key"] not in commits:
                commits[c["key"]] = dict(c, **who)
        for p in d["prs"]:
            if p["url"] not in prs:
                prs[p["url"]] = dict(p, **who)
            elif p["action"] == "created":
                prs[p["url"]]["action"] = "created"
        merges += [dict(m, **who) for m in d["merges"]]
        pushes += d["pushes"]
        passed += d["checks"]["passed"]
        failed += d["checks"]["failed"]
    if not (commits or prs or merges or pushes or passed or failed):
        return None
    ordered = sorted(commits.values(), key=lambda c: c["at"] or 0, reverse=True)
    total = cost.get("total") if cost and cost.get("enabled") else None
    per = {}
    for key, n in (("commit", len(commits)), ("pr", len(prs))):
        per[key] = {"cost": (total / n) if (total is not None and n) else None,
                    "tokens": (fresh_tokens // n) if n else None}
    return {"commits": len(commits), "prs": len(prs), "merges": len(merges), "pushes": pushes,
            "checks": {"passed": passed, "failed": failed},
            "recent_commits": ordered[:20],
            "pull_requests": sorted(prs.values(), key=lambda p: p["at"] or 0, reverse=True)[:20],
            "per": per, "currency": cost.get("currency") if total is not None else None}
