"""``parse_junit``: junit reports (shaped like pytest 9's) plus exit code -> ``PytestResult``."""

from __future__ import annotations

from pathlib import Path

import pytest

from netzero.pipeline.pytest_result import (
    MAX_FAILURES,
    OUTPUT_LINES,
    TB_LINES,
    nodeid_of,
    parse_junit,
)


def report(*cases: str) -> str:
    body = "".join(cases)
    return (
        '<?xml version="1.0" encoding="utf-8"?><testsuites name="pytest tests">'
        f'<testsuite name="pytest" errors="0" failures="0" tests="{len(cases)}">{body}'
        "</testsuite></testsuites>"
    )


def case(name: str, inner: str = "", classname: str = "test_nz_f") -> str:
    return f'<testcase classname="{classname}" name="{name}" time="0.001">{inner}</testcase>'


def parse(tmp_path: Path, xml: str | None, exit_code: int | None, output: str = ""):
    path = tmp_path / "junit.xml"
    if xml is not None:
        path.write_text(xml)
    return parse_junit(
        path, exit_code=exit_code, duration_s=1.23456, output=output, nodeid="test_nz_f.py"
    )


TRACEBACK = "\n".join(
    ["def test_b():"]
    + [f"    line {i}" for i in range(30)]
    + [
        "&gt;       assert add(1, 1) == 3",
        "E       assert 2 == 3",
        "",
        "test_nz_f.py:9: AssertionError",
    ]
)


def test_all_passed(tmp_path: Path):
    res = parse(tmp_path, report(case("test_a"), case("test_b")), 0, output="2 passed")
    assert (res.exit_code, res.passed, res.failed, res.errors, res.skipped) == (0, 2, 0, 0, 0)
    assert res.failures == []
    assert res.duration_s == 1.235
    assert res.output_tail == "2 passed"


def test_failure_message_and_traceback_tail(tmp_path: Path):
    msg = "AssertionError: first line&#10;  second line&#10;assert 2 == 3"
    failure = f'<failure message="{msg}">{TRACEBACK}</failure>'
    res = parse(tmp_path, report(case("test_a"), case("test_b", failure)), 1)
    assert (res.passed, res.failed, res.errors) == (1, 1, 0)
    [f] = res.failures
    assert f.nodeid == "test_nz_f.py::test_b"
    assert f.message == "AssertionError: first line"
    lines = f.tb_tail.splitlines()
    assert len(lines) == TB_LINES
    assert lines[-1] == "test_nz_f.py:9: AssertionError"
    assert "> " in lines[-4]


def test_failure_without_message_uses_the_text(tmp_path: Path):
    res = parse(tmp_path, report(case("test_a", "<failure>\n\nboom\nmore</failure>")), 1)
    assert res.failures[0].message == "boom"


def test_setup_error_counts_as_error(tmp_path: Path):
    err = '<error message="failed on setup with &quot;RuntimeError: setup broke&quot;">tb</error>'
    res = parse(tmp_path, report(case("test_a", err)), 1)
    assert (res.passed, res.failed, res.errors) == (0, 0, 1)
    assert res.failures[0].message == 'failed on setup with "RuntimeError: setup broke"'


def test_failure_and_teardown_error_on_one_case(tmp_path: Path):
    inner = '<failure message="assert 0">a</failure><error message="teardown broke">b</error>'
    res = parse(tmp_path, report(case("test_a", inner)), 1)
    assert (res.failed, res.errors, len(res.failures)) == (1, 1, 2)


def test_skipped(tmp_path: Path):
    skip = '<skipped type="pytest.skip" message="nah">test_nz_f.py:3: nah</skipped>'
    res = parse(tmp_path, report(case("test_a"), case("test_b", skip)), 0)
    assert (res.passed, res.skipped, res.failures) == (1, 1, [])


def test_class_and_parametrized_nodeids(tmp_path: Path):
    failure = '<failure message="assert 0">tb</failure>'
    xml = report(case("test_y[2-x]", failure, classname="test_nz_f.TestK"))
    assert parse(tmp_path, xml, 1).failures[0].nodeid == "test_nz_f.py::TestK::test_y[2-x]"


@pytest.mark.parametrize(
    ("classname", "name", "nodeid"),
    [
        ("test_nz_f", "test_a", "test_nz_f.py::test_a"),
        ("test_nz_f.TestK.TestInner", "test_a", "test_nz_f.py::TestK::TestInner::test_a"),
        ("", "test_nz_f", "test_nz_f.py"),
        ("", "conftest.py", "conftest.py"),
    ],
)
def test_nodeid_of(classname: str, name: str, nodeid: str):
    assert nodeid_of(classname, name) == nodeid


COLLECTION_TB = """ImportError while importing test module '/x/test_nz_f.py'.
Hint: make sure your test modules/packages have valid Python names.
Traceback:
test_nz_f.py:1: in &lt;module&gt;
    from pkg.funcs import add
E   ModuleNotFoundError: No module named 'pkg'"""


def test_collection_error_names_the_cause(tmp_path: Path):
    err = f'<error message="collection failure">{COLLECTION_TB}</error>'
    res = parse(tmp_path, report(case("test_nz_f", err, classname="")), 2)
    assert (res.exit_code, res.passed, res.errors) == (2, 0, 1)
    [f] = res.failures  # the report already explains exit code 2: no synthetic failure
    assert f.nodeid == "test_nz_f.py"
    assert f.message == "collection failure: ModuleNotFoundError: No module named 'pkg'"


def test_syntax_error_at_collection(tmp_path: Path):
    tb = '  File "/x/test_nz_f.py", line 3\n    def (:\n        ^\nE   SyntaxError: invalid syntax'
    err = f'<error message="collection failure">{tb}</error>'
    res = parse(tmp_path, report(case("test_nz_f", err, classname="")), 2)
    assert res.failures[0].message == "collection failure: SyntaxError: invalid syntax"


def test_missing_report_gets_a_synthetic_error(tmp_path: Path):
    output = "collecting ...\nE   ImportError: cannot import name 'x'\nInterrupted: 1 error"
    res = parse(tmp_path, None, 2, output=output)
    assert (res.exit_code, res.passed, res.errors) == (2, 0, 1)
    [f] = res.failures
    assert f.nodeid == "test_nz_f.py"
    # the last line naming an exception wins, without its E marker
    reason = "pytest was interrupted (usually a collection error)"
    assert f.message == f"{reason}: ImportError: cannot import name 'x'"
    assert f.tb_tail == output
    assert parse(tmp_path, None, 2, output="bye").failures[0].message == reason


def test_corrupt_report(tmp_path: Path):
    res = parse(tmp_path, "<testsuites><testsuite>", 1, output="")
    assert res.errors == 1
    assert res.failures[0].message == "pytest wrote no report (exit code 1)"


def test_timeout(tmp_path: Path):
    res = parse(tmp_path, None, None, output="still running")
    assert res.exit_code == -1 and res.errors == 1
    assert res.failures[0].message == "pytest timed out"


def test_killed_by_signal(tmp_path: Path):
    res = parse(tmp_path, report(case("test_a")), -9)
    assert (res.passed, res.errors) == (1, 1)
    assert res.failures[0].message == "pytest was killed by SIGKILL"
    assert parse(tmp_path, None, -200).failures[0].message == "pytest was killed by signal 200"


def test_no_tests_collected(tmp_path: Path):
    res = parse(tmp_path, report(), 5)
    assert res.errors == 1 and res.failures[0].message == "no tests were collected"


def test_failed_exit_without_listed_failures(tmp_path: Path):
    res = parse(tmp_path, report(case("test_a")), 1)
    assert res.errors == 1
    assert res.failures[0].message == "tests failed but the report lists no failure"
    assert parse(tmp_path, report(case("test_a")), 7).failures[0].message == (
        "pytest exited with code 7"
    )


def test_output_tail_is_bounded(tmp_path: Path):
    output = "\n".join(f"line {i}" for i in range(200))
    res = parse(tmp_path, report(case("test_a")), 0, output=output)
    lines = res.output_tail.splitlines()
    assert len(lines) == OUTPUT_LINES and lines[-1] == "line 199"


def test_failures_are_capped(tmp_path: Path):
    cases = [case(f"test_{i}", '<failure message="no">tb</failure>') for i in range(50)]
    res = parse(tmp_path, report(*cases), 1)
    assert res.failed == 50
    assert len(res.failures) == MAX_FAILURES


def test_long_messages_are_cut(tmp_path: Path):
    res = parse(tmp_path, report(case("test_a", f'<failure message="{"x" * 5000}">t</failure>')), 1)
    assert len(res.failures[0].message) <= 500
