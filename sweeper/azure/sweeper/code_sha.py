"""Hash of the sweeper's own code.

OpenTofu computes the same value as `local.sweeper_src_sha` (terraform/azure/sweeper.tf) and
exposes it as the `sweeper_code_sha` output. The sweeper logs this value in every invocation, so
`make verify/azure-sweeper` can tell whether the running code is the code in state. Both sides
must list the same files in the same way: `function_app.py`, `host.json`, `requirements.txt`, and
every `.py` file of the `sweeper/` package; sha256 of `<path>:<sha256 of the file>` entries joined
by `,`, sorted by path with `/` separators.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

_SKIP_DIRS = {".venv", "__pycache__", "tests", ".pytest_cache", ".ruff_cache", ".python_packages"}
_TOP_FILES = ("function_app.py", "host.json", "requirements.txt")


def _files(root: Path) -> list[str]:
    names = [name for name in _TOP_FILES if (root / name).is_file()]
    for path in (root / "sweeper").rglob("*.py"):
        rel = path.relative_to(root)
        if not _SKIP_DIRS.intersection(rel.parts):
            names.append(rel.as_posix())
    return sorted(names)


def code_sha(root: Path | None = None) -> str:
    """The hash of the code under `root` (default: the folder that holds `function_app.py`)."""
    root = root or Path(__file__).resolve().parent.parent
    entries = [
        f"{name}:{hashlib.sha256((root / name).read_bytes()).hexdigest()}" for name in _files(root)
    ]
    return hashlib.sha256(",".join(entries).encode()).hexdigest()
