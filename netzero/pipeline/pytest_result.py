"""Turn a pytest run (``--junitxml`` report + exit code + output) into ``PytestResult``.

A run without a usable report (collection crash, a killed interpreter, a
timeout), or one whose exit code says something went wrong that the report
does not show, gets ``errors >= 1`` and a synthetic failure built from the
output tail, so "no failures listed" always means the tests really passed.
"""

from __future__ import annotations

import re
import signal
import xml.etree.ElementTree as ET
from pathlib import Path

from netzero.events import PytestFailure, PytestResult

TB_LINES = 15
OUTPUT_LINES = 60
MAX_FAILURES = 20
MAX_MESSAGE = 500
MAX_REPORT_BYTES = 32 * 1024 * 1024

EXIT_REASONS = {
    2: "pytest was interrupted (usually a collection error)",
    3: "pytest hit an internal error",
    4: "pytest usage error",
    5: "no tests were collected",
}
_ERROR_LINE = re.compile(r"^E\s+\S|\b\w*(Error|Exception|Interrupt)\b|^Fatal Python error")
_E_PREFIX = re.compile(r"^E\s+")


def tail(text: str, n: int) -> str:
    return "\n".join(text.splitlines()[-n:])


def _first_line(text: str) -> str:
    for line in text.splitlines():
        if line.strip():
            return line.strip()[:MAX_MESSAGE]
    return ""


def nodeid_of(classname: str, name: str) -> str:
    """Rebuild a nodeid from junit's mangled ``classname``. Assumes the test file sits
    in the rootdir (as the generated ones do), so the first dotted part is the file."""
    if not classname:  # a module-level error: ``name`` is the file without ``.py``
        return name if name.endswith(".py") else f"{name}.py"
    file, *rest = classname.split(".")
    return "::".join([f"{file}.py", *rest, name])


def _error_line(text: str) -> str:
    """The last ``E   ...`` line of a traceback, without the ``E``."""
    for line in reversed(text.splitlines()):
        if _E_PREFIX.match(line):
            return _E_PREFIX.sub("", line).strip()
    return ""


def _failure(nodeid: str, elem: ET.Element) -> PytestFailure:
    text = elem.text or ""
    message = _first_line(elem.get("message") or "") or _first_line(text)
    if message == "collection failure" and (cause := _error_line(text)):
        message = f"{message}: {cause}"  # junit's message alone does not say what broke
    return PytestFailure(nodeid=nodeid, message=message[:MAX_MESSAGE], tb_tail=tail(text, TB_LINES))


def _read_report(path: Path) -> ET.Element | None:
    try:
        if path.stat().st_size > MAX_REPORT_BYTES:
            return None
        return ET.parse(path).getroot()
    except (OSError, ET.ParseError):
        return None


def _crash_reason(exit_code: int | None, have_report: bool) -> str:
    if exit_code is None:
        return "pytest timed out"
    if exit_code < 0:
        try:
            return f"pytest was killed by {signal.Signals(-exit_code).name}"
        except ValueError:
            return f"pytest was killed by signal {-exit_code}"
    if exit_code in EXIT_REASONS:
        return EXIT_REASONS[exit_code]
    if not have_report:
        return f"pytest wrote no report (exit code {exit_code})"
    if exit_code == 1:
        return "tests failed but the report lists no failure"
    return f"pytest exited with code {exit_code}"


def synthetic_failure(nodeid: str, reason: str, output: str) -> PytestFailure:
    """A failure for what the report cannot show, quoting the most telling output line."""
    lines = [line.strip() for line in output.splitlines() if line.strip()]
    hint = next((line for line in reversed(lines) if _ERROR_LINE.search(line)), "")
    hint = _E_PREFIX.sub("", hint)
    message = f"{reason}: {hint}" if hint else reason
    return PytestFailure(
        nodeid=nodeid, message=message[:MAX_MESSAGE], tb_tail=tail(output, TB_LINES)
    )


def parse_junit(
    xml_path: Path,
    *,
    exit_code: int | None,
    duration_s: float,
    output: str,
    nodeid: str = "pytest",
) -> PytestResult:
    """``exit_code`` is None for a timeout (reported as -1); ``nodeid`` names the
    synthetic failure (e.g. the test file)."""
    root = _read_report(xml_path)
    counts = {"passed": 0, "failed": 0, "errors": 0, "skipped": 0}
    failures: list[PytestFailure] = []
    for case in root.iter("testcase") if root is not None else ():
        nid = nodeid_of(case.get("classname", ""), case.get("name", ""))
        failure, error = case.find("failure"), case.find("error")
        if failure is not None:
            counts["failed"] += 1
            failures.append(_failure(nid, failure))
        if error is not None:
            counts["errors"] += 1
            failures.append(_failure(nid, error))
        if failure is None and error is None:
            counts["skipped" if case.find("skipped") is not None else "passed"] += 1
    if root is None or (exit_code != 0 and counts["failed"] + counts["errors"] == 0):
        counts["errors"] += 1
        failures.append(
            synthetic_failure(nodeid, _crash_reason(exit_code, root is not None), output)
        )
    return PytestResult(
        exit_code=-1 if exit_code is None else exit_code,
        duration_s=round(duration_s, 3),
        failures=failures[:MAX_FAILURES],
        output_tail=tail(output, OUTPUT_LINES),
        **counts,
    )
