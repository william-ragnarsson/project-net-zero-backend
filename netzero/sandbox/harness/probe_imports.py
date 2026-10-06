"""Import each module of the repo and report one line per module.

stdin: ``{"roots": [import roots], "modules": [dotted names]}``. The roots go
to the front of ``sys.path`` only after this script's own imports, so a repo
file named like a stdlib module cannot break the harness (as in pytest's
``prepend`` import mode).

stdout: ``__NZ__ {"module": ..., "ok": bool, "error": str | null}`` per module,
each on a fresh line. ``print`` during an import goes to stderr, so a module's
output cannot split or forge a record. Results stream as they happen, so a
module that kills the interpreter is the first one without a result.
"""

from __future__ import annotations

import importlib
import json
import os
import signal
import sys

MARK = "__NZ__ "
PER_MODULE_S = 10
_OUT = sys.stdout  # records only; imports see stderr as stdout


class _Timeout(BaseException):
    pass


def _alarm(signum, frame):  # noqa: ARG001
    raise _Timeout()


def _report(module: str, ok: bool, error: str | None) -> None:
    rec = json.dumps({"module": module, "ok": ok, "error": error})
    _OUT.write(f"\n{MARK}{rec}\n")  # leading newline: a partial line from os.write stays apart
    _OUT.flush()


def _inside(path: str, roots: list[str]) -> bool:
    real = os.path.realpath(path)
    return any(real.startswith(root + os.sep) for root in roots)


def _check(name: str, roots: list[str]) -> str | None:
    """None if ``name`` imports from the repo's own source, else why not."""
    mod = importlib.import_module(name)
    path = getattr(mod, "__file__", None)
    if not path:
        return None  # namespace package
    if not path.endswith(".py"):
        return f"a compiled extension shadows the source ({path})"
    if roots and not _inside(path, roots):
        return f"resolves to {path}, outside the repo (shadowed by another module)"
    return None


def main() -> None:
    req = json.load(sys.stdin)
    sys.stdout = sys.stderr
    roots = [os.path.realpath(r) for r in req.get("roots", [])]
    sys.path[:0] = [r for r in roots if r not in sys.path]
    signal.signal(signal.SIGALRM, _alarm)
    for name in req["modules"]:
        signal.alarm(PER_MODULE_S)
        try:
            problem = _check(name, roots)
            _report(name, problem is None, problem)
        except _Timeout:
            _report(name, False, f"import took longer than {PER_MODULE_S} s")
        except BaseException as exc:  # SystemExit and KeyboardInterrupt included
            _report(name, False, f"{type(exc).__name__}: {exc}"[:400])
        finally:
            signal.alarm(0)


if __name__ == "__main__":
    main()
