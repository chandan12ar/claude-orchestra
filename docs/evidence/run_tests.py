"""Runs the whole test suite and writes docs/evidence/test-run.txt.

    python docs/evidence/run_tests.py

The file records WHEN and WHAT was tested (commit, Python, Node, OS), a per-module
summary, and every test by name with its result, so it can be attached to a PR as proof
of a local run. It is a local run only; it says nothing about CI.
"""

import os
import platform
import re
import subprocess
import sys
from collections import OrderedDict
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
OUT = os.path.join(HERE, "test-run.txt")
LINE = re.compile(r"^(?P<name>\S+) \((?P<path>[\w.]+)\)(?:\n.*?)? \.\.\. (?P<result>ok|FAIL|ERROR|skipped.*|expected failure|unexpected success)$")


def sh(*cmd):
    try:
        return subprocess.run(cmd, cwd=ROOT, capture_output=True, encoding="utf-8").stdout.strip()
    except OSError:
        return "unavailable"


def main():
    env = dict(os.environ, PYTHONWARNINGS="ignore", PYTHONIOENCODING="utf-8")
    started = datetime.now(timezone.utc)
    proc = subprocess.run([sys.executable, "-m", "unittest", "discover", "-s", "tests", "-t", ".", "-v"],
                          cwd=ROOT, capture_output=True, encoding="utf-8", env=env)
    text = proc.stderr
    results = []
    # Split the output at each "name (path)" header and read the result from the end of
    # that segment. A test can print in the middle of its own line (a deliberate server
    # traceback) or wrap onto a docstring line, so a one-line pattern misses some.
    heads = list(re.finditer(r"^(\S+) \(([\w.]+)\)", text, re.M))
    trailer = re.search(r"\n-{70}\nRan \d+ tests?", text)          # the summary after the last test
    stop = trailer.start() if trailer else len(text)
    for i, head in enumerate(heads):
        end = heads[i + 1].start() if i + 1 < len(heads) else stop
        segment = text[head.start():end].rstrip()
        tail = re.search(r"(?:\.\.\. |\n)(ok|FAIL|ERROR|skipped[^\n]*)$", segment)
        if not tail:
            continue
        path = head.group(2)
        results.append((".".join(path.split(".")[:2]), path, tail.group(1)))
    summary = re.search(r"^Ran (\d+) tests? in ([\d.]+)s", text, re.M)
    verdict = [l for l in text.splitlines() if l.startswith(("OK", "FAILED"))]
    by_module = OrderedDict()
    for module, _, result in results:
        slot = by_module.setdefault(module, {"ok": 0, "skipped": 0, "failed": 0})
        slot["ok" if result == "ok" else "skipped" if result.startswith("skipped") else "failed"] += 1

    lines = [
        "Workflow - local test run",
        "=" * 60,
        "when      : " + started.strftime("%Y-%m-%d %H:%M:%S UTC"),
        "commit    : " + sh("git", "rev-parse", "HEAD") + "  (" + sh("git", "rev-parse", "--abbrev-ref", "HEAD") + ")",
        "worktree  : " + ("clean" if not sh("git", "status", "--porcelain", "--", ".", ":!docs/evidence") else "has uncommitted changes"),
        "python    : " + platform.python_version(),
        "node      : " + sh("node", "--version"),
        "os        : " + platform.platform(),
        "command   : python -m unittest discover -s tests -t . -v",
        "scope     : LOCAL run only. This says nothing about GitHub CI.",
        "",
        summary.group(0) if summary else "(no summary line found)",
        " ".join(verdict) if verdict else "(no verdict line found)",
        "",
        "Per module",
        "-" * 60,
        "{:<34} {:>6} {:>8} {:>7}".format("module", "passed", "skipped", "failed"),
    ]
    for module, c in by_module.items():
        lines.append("{:<34} {:>6} {:>8} {:>7}".format(module, c["ok"], c["skipped"], c["failed"]))
    total = {k: sum(c[k] for c in by_module.values()) for k in ("ok", "skipped", "failed")}
    lines.append("{:<34} {:>6} {:>8} {:>7}".format("TOTAL", total["ok"], total["skipped"], total["failed"]))
    lines += ["", "Every test", "-" * 60]
    lines += ["{:<8} {}".format(r if r == "ok" else r.split(" ")[0].upper(), p) for _, p, r in results]
    lines += ["", "Notes",
              "-" * 60,
              "Some tests deliberately make the server raise (a corrupt transcript, a handler that",
              "throws) to prove it answers 500 instead of hanging; their tracebacks are expected",
              "and are not included here.",
              "Three tests skip on Windows: they check POSIX permissions, ownership and symlinks."]
    with open(OUT, "w", encoding="utf-8", newline="\n") as fh:
        fh.write("\n".join(lines) + "\n")
    print("\n".join(lines[:16]))
    print("wrote", OUT)
    return proc.returncode


if __name__ == "__main__":
    sys.exit(main())
