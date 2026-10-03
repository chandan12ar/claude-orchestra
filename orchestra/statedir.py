"""Where Cuelight keeps its own runtime files (port files, logs, the event spool).

Never under ~/.claude. Per-user, created 0700, and refused if it already exists
but belongs to someone else: the system temp directory is world-writable, so a
fixed, guessable path there would let another local user pre-create it and read
or feed the files inside.
"""

import os
import stat
import tempfile


class UnsafeStateDir(OSError):
    """The state directory exists but is not exclusively ours."""


def _user_suffix() -> str:
    if hasattr(os, "getuid"):
        return str(os.getuid())
    name = os.environ.get("USERNAME") or os.environ.get("USER") or "user"
    return "".join(ch for ch in name if ch.isalnum() or ch in "-_") or "user"


def state_dir() -> str:
    override = os.environ.get("ORCHESTRA_STATE_DIR")
    directory = override or os.path.join(
        tempfile.gettempdir(), "orchestra-" + _user_suffix())
    os.makedirs(directory, mode=0o700, exist_ok=True)
    if os.name == "posix":
        info = os.lstat(directory)
        if stat.S_ISLNK(info.st_mode) or info.st_uid != os.getuid():
            raise UnsafeStateDir(
                "{} is not owned by the current user".format(directory))
        if info.st_mode & 0o077:
            os.chmod(directory, 0o700)
    return directory
