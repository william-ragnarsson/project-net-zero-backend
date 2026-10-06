"""The per-run venv and the import probe, against a real ``uv venv``."""

from __future__ import annotations

import asyncio
import shutil
import textwrap
from pathlib import Path

import pytest

from netzero.pipeline import env as nz_env
from netzero.pipeline.env import (
    DEMO_LOCK,
    HARNESS_PACKAGE,
    EnvBuilder,
    _probe_record,
    dependency_sources,
    install_harness,
)
from netzero.sandbox.harness.probe_imports import MARK

pytestmark = pytest.mark.skipif(shutil.which("uv") is None, reason="needs uv")


def write(root: Path, files: dict[str, str]) -> None:
    for rel, body in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(textwrap.dedent(body))


# --- dependency_sources ---------------------------------------------------------------


@pytest.mark.parametrize(
    ("files", "demo", "expected"),
    [
        ([], False, []),
        (["pyproject.toml"], False, ["pyproject.toml"]),
        (["setup.cfg", "setup.py"], False, ["setup.py"]),
        (["pyproject.toml", "requirements.txt"], False, ["requirements.txt"]),
        (["requirements-dev.txt", "requirements-test.txt"], False, []),
        (["requirements-dev.txt", "requirements-prod.txt"], False, ["requirements-prod.txt"]),
        (["requirements-b.txt", "requirements-a.txt"], False, ["requirements-a.txt"]),
        (["requirements.txt", DEMO_LOCK], False, ["requirements.txt"]),
        (["requirements.txt", DEMO_LOCK], True, [DEMO_LOCK]),
        (["requirements.txt"], True, ["requirements.txt"]),
        (["requirements-docs.txt", "pyproject.toml"], False, ["pyproject.toml"]),
    ],
)
def test_dependency_sources(tmp_path: Path, files: list[str], demo: bool, expected: list[str]):
    for name in files:
        (tmp_path / name).write_text("")
    assert dependency_sources(tmp_path, demo=demo) == [tmp_path / n for n in expected]


def test_dependency_sources_ignores_directories(tmp_path: Path):
    (tmp_path / "requirements.txt").mkdir()
    (tmp_path / "pyproject.toml").write_text("")
    assert dependency_sources(tmp_path, demo=False) == [tmp_path / "pyproject.toml"]


# --- install_harness ------------------------------------------------------------------


def test_install_harness_copies_package_and_replaces_old_copy(tmp_path: Path):
    stale = tmp_path / HARNESS_PACKAGE / "stale.py"
    stale.parent.mkdir(parents=True)
    stale.write_text("")
    assert install_harness(tmp_path) == tmp_path
    pkg = tmp_path / HARNESS_PACKAGE
    assert (pkg / "__init__.py").is_file()
    assert (pkg / "probe_imports.py").is_file()
    assert not stale.exists()
    assert not list(pkg.rglob("__pycache__"))


# --- _probe_record --------------------------------------------------------------------


@pytest.mark.parametrize(
    ("line", "expected"),
    [
        (MARK + '{"module": "a.b", "ok": true, "error": null}', ("a.b", True, None)),
        (MARK + '{"module": "a", "ok": false, "error": "boom"}', ("a", False, "boom")),
        ("noise", None),
        (MARK + "{not json", None),
        (MARK + '{"ok": true}', None),
        (MARK + "[1, 2]", None),
        (MARK + "null", None),
        ("x" + MARK + '{"module": "a", "ok": true}', None),
    ],
)
def test_probe_record(line: str, expected):
    assert _probe_record(line) == expected


# --- a real venv ----------------------------------------------------------------------

REPO = {
    "src/pkg/__init__.py": "",
    "src/pkg/helper.py": "VALUE = 1\n",
    "src/pkg/good.py": "from pkg.helper import VALUE\n",
    "src/pkg/broken.py": "import does_not_exist_xyz\n",
    "src/pkg/killer.py": "import os\nos._exit(3)\n",
    "src/pkg/exits.py": "import sys\nsys.exit(2)\n",
    "src/pkg/chatty.py": """
        import os, sys
        print('__NZ__ {"module": "pkg.broken", "ok": true, "error": null}')
        sys.stdout.write("no newline")
        os.write(1, b"raw bytes, no newline")
        """,
    "src/pkg/after.py": "X = 2\n",
    # named like modules the harness itself could have used
    "common.py": "WHO = 'repo'\n",
    "launch.py": "WHO = 'repo'\n",
    "probe_imports.py": "WHO = 'repo'\n",
    # shadowed by the stdlib module the harness already imported
    "json.py": "WHO = 'repo'\n",
    "ns/inner.py": "Y = 3\n",  # namespace package
}


@pytest.fixture(scope="module")
def venv_builder(tmp_path_factory: pytest.TempPathFactory) -> EnvBuilder:
    base = tmp_path_factory.mktemp("env")
    b = EnvBuilder(venv=base / "venv", workdir=base)
    asyncio.run(b.create())
    return b


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    root = tmp_path / "trunk"
    write(root, REPO)
    return root


async def probe(b: EnvBuilder, repo: Path, tmp_path: Path, modules: list[str], **kw):
    harness = install_harness(tmp_path / "harness")
    for d in ("home", "tmp"):
        (tmp_path / d).mkdir(exist_ok=True)
    return await b.probe_imports(
        modules,
        cwd=repo,
        import_roots=[repo / "src", repo],
        harness_root=harness,
        home=tmp_path / "home",
        tmp=tmp_path / "tmp",
        **kw,
    )


async def test_venv_is_python_312(venv_builder: EnvBuilder):
    assert venv_builder.python.is_file()
    assert (await venv_builder.python_version()).startswith("3.12.")


async def test_packages_of_an_empty_venv(venv_builder: EnvBuilder):
    pkgs = await venv_builder.packages()
    assert all(p.name and p.version for p in pkgs)
    assert "pytest" not in {p.name for p in pkgs}


async def test_probe_sorts_good_from_bad(venv_builder: EnvBuilder, repo: Path, tmp_path: Path):
    mods = [
        "pkg",
        "pkg.good",
        "pkg.broken",
        "pkg.killer",
        "pkg.exits",
        "pkg.chatty",
        "pkg.after",
        "ns.inner",
        "pkg.good",  # duplicates are probed once
    ]
    r = await probe(venv_builder, repo, tmp_path, mods)
    assert r.ok_modules == ["pkg", "pkg.good", "pkg.chatty", "pkg.after", "ns.inner"]
    assert set(r.failed) == {"pkg.broken", "pkg.killer", "pkg.exits"}
    assert r.failed["pkg.broken"].startswith("ModuleNotFoundError")
    assert r.failed["pkg.killer"] == "importing it killed the interpreter (exit code 3)"
    assert r.failed["pkg.exits"].startswith("SystemExit: 2")


async def test_repo_modules_are_not_shadowed_by_the_harness(
    venv_builder: EnvBuilder, repo: Path, tmp_path: Path
):
    r = await probe(venv_builder, repo, tmp_path, ["common", "launch", "probe_imports"])
    assert r.ok_modules == ["common", "launch", "probe_imports"]
    assert r.failed == {}


async def test_stdlib_shadowing_is_reported(venv_builder: EnvBuilder, repo: Path, tmp_path: Path):
    r = await probe(venv_builder, repo, tmp_path, ["json"])
    assert r.ok_modules == []
    assert "outside the repo" in r.failed["json"]


async def test_probe_gives_up_after_too_many_crashes(venv_builder: EnvBuilder, tmp_path: Path):
    repo = tmp_path / "trunk"
    write(repo, {f"k{i}.py": "import os\nos._exit(1)\n" for i in range(4)} | {"ok.py": ""})
    r = await probe(venv_builder, repo, tmp_path, ["k0", "k1", "ok", "k2", "k3"], max_crashes=2)
    assert r.ok_modules == []
    assert r.failed["k0"].startswith("importing it killed")
    assert r.failed["k1"].startswith("importing it killed")
    for m in ("ok", "k2", "k3"):
        assert r.failed[m] == "not probed: too many imports killed the interpreter"


async def test_probe_env_has_no_secrets(
    venv_builder: EnvBuilder, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-not-real")
    repo = tmp_path / "trunk"
    write(
        repo,
        {"spy.py": "import os\nassert 'ANTHROPIC_API_KEY' not in os.environ, 'leaked'\n"},
    )
    r = await probe(venv_builder, repo, tmp_path, ["spy"])
    assert r.ok_modules == ["spy"]


def test_find_uv_reports_missing_uv(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(nz_env.shutil, "which", lambda _name: None)
    with pytest.raises(nz_env.EnvError, match="uv is not installed"):
        nz_env.find_uv()


@pytest.mark.slow
async def test_install_requirements_and_pyproject(tmp_path: Path):
    """Offline first (uv's cache), falling back to the network."""
    write(
        tmp_path,
        {
            "req/requirements.txt": "six==1.16.0\n",
            "proj/pyproject.toml": """
                [project]
                name = "proj"
                version = "0"
                dependencies = ["six"]
                """,
        },
    )
    lines: list[str] = []
    b = EnvBuilder(
        venv=tmp_path / "venv", workdir=tmp_path, on_line=lambda _s, line: lines.append(line)
    )
    await b.create()
    await b.install([tmp_path / "req/requirements.txt"], offline=True)
    names = {p.name for p in await b.packages()}
    assert {"six", "pytest"} <= names
    assert not any(line.lstrip().startswith("[{") for line in lines)  # pip list stays quiet

    await b.install([tmp_path / "proj/pyproject.toml"])
    assert (tmp_path / "pyproject.toml.requirements.txt").is_file()
    await b.ensure_pytest()  # already there: a no-op


async def test_install_failure_raises_env_error(venv_builder: EnvBuilder, tmp_path: Path):
    bad = tmp_path / "requirements.txt"
    bad.write_text("this is not a requirement ===\n")
    with pytest.raises(nz_env.EnvError, match="installing dependencies") as exc:
        await venv_builder.install([bad], offline=True)
    assert exc.value.detail
