"""GitHub URL and ref validation, repo limits."""

from __future__ import annotations

from pathlib import Path

import pytest

from netzero.errors import CloneError
from netzero.pipeline.clone import (
    GithubRepo,
    InvalidUrl,
    check_limits,
    parse_github_url,
    repo_stats,
    validate_ref,
)


@pytest.mark.parametrize(
    ("url", "owner", "repo"),
    [
        ("https://github.com/psf/requests", "psf", "requests"),
        ("https://github.com/psf/requests/", "psf", "requests"),
        ("https://github.com/psf/requests.git", "psf", "requests"),
        ("  https://github.com/a-b/c.d_e  ", "a-b", "c.d_e"),
        ("https://github.com/o/repo.git/", "o", "repo"),
    ],
)
def test_parse_github_url_accepts(url: str, owner: str, repo: str):
    assert parse_github_url(url) == GithubRepo(owner, repo)


@pytest.mark.parametrize(
    "url",
    [
        "",
        "github.com/psf/requests",
        "http://github.com/psf/requests",
        "https://gitlab.com/psf/requests",
        "https://github.com/psf",
        "https://github.com/psf/requests/tree/main",
        "https://github.com/psf/requests?x=1",
        "https://github.com/-psf/requests",
        "https://github.com/psf/..",
        "https://github.com/psf/.",
        "https://user@github.com/psf/requests",
        "https://github.com.evil.com/psf/requests",
        "file:///etc/passwd",
        "https://github.com/psf/re quests",
    ],
)
def test_parse_github_url_rejects(url: str):
    with pytest.raises(InvalidUrl) as exc:
        parse_github_url(url)
    assert exc.value.kind == "validation"


def test_github_repo_url_and_label():
    r = GithubRepo("psf", "requests")
    assert r.url == "https://github.com/psf/requests"
    assert r.label == "psf-requests"


@pytest.mark.parametrize("ref", [None, ""])
def test_validate_ref_empty(ref):
    assert validate_ref(ref) is None


@pytest.mark.parametrize("ref", ["main", "v1.2.3", "feature/x-y_z", "a" * 200])
def test_validate_ref_accepts(ref: str):
    assert validate_ref(ref) == ref


@pytest.mark.parametrize(
    "ref", ["-x", "--upload-pack=evil", "/abs", "a..b", "a b", "a;b", "a" * 201, "ref~1"]
)
def test_validate_ref_rejects(ref: str):
    with pytest.raises(InvalidUrl):
        validate_ref(ref)


def tree(root: Path, files: dict[str, int]) -> None:
    for rel, size in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"x" * size)


def test_repo_stats_ignores_git_dir(tmp_path: Path):
    tree(tmp_path, {"a.py": 10, "b/c.py": 5, "README": 3, ".git/objects/x": 1000})
    assert repo_stats(tmp_path) == (3, 2, 18)


def test_check_limits(tmp_path: Path):
    tree(tmp_path, {"a.py": 10, "b.txt": 10})
    assert check_limits(tmp_path, max_mb=1, max_files=2) == (2, 1, 20)
    with pytest.raises(CloneError, match="2 files; the limit is 1"):
        check_limits(tmp_path, max_mb=1, max_files=1)


def test_check_limits_size(tmp_path: Path):
    tree(tmp_path, {"a.py": 2 * 1024 * 1024})
    with pytest.raises(CloneError, match="limit is 1 MB"):
        check_limits(tmp_path, max_mb=1, max_files=10)


def test_check_limits_needs_python(tmp_path: Path):
    tree(tmp_path, {"README.md": 10})
    with pytest.raises(CloneError, match="no Python files"):
        check_limits(tmp_path, max_mb=1, max_files=10)
