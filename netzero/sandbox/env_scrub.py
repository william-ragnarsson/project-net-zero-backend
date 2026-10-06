"""Environments for child processes.

Two kinds:

* ``sandbox_env``: for the target repo's code (pytest, capture, bench workers).
  Built from an allow-list, never inherited: no API keys, tokens, ``CODECARBON_*``
  or ``PYTHON*`` beyond what is set here. ``HOME``/``TMPDIR`` point into the run.
* ``tool_env``: for trusted tools (git, uv). Inherits the environment minus
  anything secret-looking, so uv keeps its cache and managed Pythons.
"""

from __future__ import annotations

import os
import re
from collections.abc import Iterable, Mapping
from pathlib import Path

SANDBOX_PASSTHROUGH = ("LANG", "LC_ALL", "LC_CTYPE", "TZ")

SYSTEM_PATH = ("/usr/local/bin", "/usr/bin", "/bin", "/usr/sbin", "/sbin")

SINGLE_THREAD = {
    "OMP_NUM_THREADS": "1",
    "MKL_NUM_THREADS": "1",
    "OPENBLAS_NUM_THREADS": "1",
    "NUMEXPR_NUM_THREADS": "1",
    "VECLIB_MAXIMUM_THREADS": "1",
}

SECRET_RE = re.compile(
    r"(KEY|TOKEN|SECRET|PASSWORD|PASSWD|CREDENTIAL|AUTH|COOKIE|SESSION)", re.IGNORECASE
)
DROP_PREFIXES = (
    "ANTHROPIC_",
    "NETZERO_",
    "CODECARBON_",
    "AWS_",
    "AZURE_",
    "GOOGLE_",
    "GCP_",
    "GITHUB_",
    "GH_",
    "OPENAI_",
    "PYTHON",
    "VIRTUAL_ENV",
    "CONDA_",
    "PIP_",
)


def venv_bin(venv: Path) -> Path:
    return venv / "bin"


def venv_python(venv: Path) -> Path:
    return venv_bin(venv) / "python"


def sandbox_env(
    *,
    venv: Path,
    home: Path,
    tmp: Path,
    pythonpath: Iterable[Path] = (),
    extra: Mapping[str, str] | None = None,
) -> dict[str, str]:
    """Allow-listed environment for running the target repo's code."""
    env = {k: os.environ[k] for k in SANDBOX_PASSTHROUGH if k in os.environ}
    env.setdefault("LANG", "C.UTF-8")
    env.update(
        {
            "PATH": os.pathsep.join([str(venv_bin(venv)), *SYSTEM_PATH]),
            "HOME": str(home),
            "TMPDIR": str(tmp),
            "TMP": str(tmp),
            "TEMP": str(tmp),
            "VIRTUAL_ENV": str(venv),
            "PYTHONHASHSEED": "0",  # set/dict-of-str order must match across runs
            "PYTHONDONTWRITEBYTECODE": "1",  # keep worktrees clean for git diff
            "PYTHONNOUSERSITE": "1",
            "PYTHONUTF8": "1",
            "PYTHONUNBUFFERED": "1",
            "NO_COLOR": "1",
            **SINGLE_THREAD,
        }
    )
    pp = [str(p) for p in pythonpath]
    if pp:
        env["PYTHONPATH"] = os.pathsep.join(pp)
    if extra:
        env.update(extra)
    return env


def is_secret_name(name: str) -> bool:
    return name.startswith(DROP_PREFIXES) or bool(SECRET_RE.search(name))


def tool_env(extra: Mapping[str, str] | None = None) -> dict[str, str]:
    """The current environment without secrets, for git and uv."""
    env = {k: v for k, v in os.environ.items() if not is_secret_name(k)}
    env.update(
        {
            "GIT_TERMINAL_PROMPT": "0",
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": os.devnull,  # no credential helpers, hooks or aliases
            "GIT_ASKPASS": "/usr/bin/false",
            "SSH_ASKPASS": "/usr/bin/false",
            "UV_NO_PROGRESS": "1",
            "NO_COLOR": "1",
        }
    )
    if extra:
        env.update(extra)
    return env
