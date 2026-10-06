"""Cheap checks on what the LLM wrote, before anything runs in the sandbox.

Pure AST, nothing is imported or executed. A test file must hold a few tests
and exactly one ``nz_workload`` test; it gets the imports it forgot. A
candidate must be a drop-in replacement for the original def (same name,
parameters, decorators, async-ness) that adds no global state or I/O and
imports only what it may. Each problem is a sentence the repair prompt can
quote back to the model.
"""

from __future__ import annotations

import ast
import builtins
import copy
import difflib
import re
import sys
import textwrap
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field

from netzero.events import StaticCheck
from netzero.pipeline.discovery import DiscoveredFunction
from netzero.pipeline.splice import find_function_node

MIN_TESTS = 3
WORKLOAD_MARK = "mark.nz_workload"

FORBIDDEN_MODULES = frozenset(
    {
        "socket", "ssl", "urllib", "urllib3", "http", "requests", "httpx", "aiohttp",
        "ftplib", "smtplib", "poplib", "imaplib", "telnetlib", "xmlrpc", "socketserver",
        "webbrowser", "subprocess", "multiprocessing", "concurrent", "pty", "shutil",
        "tempfile", "sqlite3", "ctypes",
    }
)  # fmt: skip
FORBIDDEN_CALLS = frozenset(
    {
        "open", "eval", "exec", "__import__", "breakpoint", "input",
        "os.system", "os.popen", "os.remove", "os.unlink", "os.rmdir", "os.removedirs",
        "os.mkdir", "os.makedirs", "os.rename", "os.replace", "os.fork", "os.kill",
    }
)  # fmt: skip
IO_CALLS = frozenset({"print"}) | FORBIDDEN_CALLS  # a candidate may keep what the original did
MOCK_MODULES = frozenset({"unittest.mock", "mock", "pytest_mock"})
_MODULE_DUNDERS = frozenset(
    {"__file__", "__name__", "__doc__", "__spec__", "__loader__", "__package__", "__class__"}
)
_FENCE = re.compile(r"\A\s*```[\w+-]*[ \t]*\n(.*?)\n?\s*```\s*\Z", re.S)

FuncNode = ast.FunctionDef | ast.AsyncFunctionDef


def strip_fences(code: str) -> str:
    """Drop a markdown fence around the whole text, which models add despite being told not to."""
    m = _FENCE.match(code)
    return m.group(1) if m else code


def _dotted(node: ast.AST) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        base = _dotted(node.value)
        return f"{base}.{node.attr}" if base else None
    return None


def _aliases(tree: ast.AST) -> dict[str, str]:
    """Local name -> what it was imported as, e.g. ``{"sp": "subprocess", "rm": "os.remove"}``."""
    out: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                out[a.asname or a.name.split(".")[0]] = a.name if a.asname else a.name.split(".")[0]
        elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
            for a in node.names:
                out[a.asname or a.name] = f"{node.module}.{a.name}"
    return out


def _calls(tree: ast.AST, aliases: dict[str, str]) -> set[str]:
    """Dotted names of everything called, with import aliases resolved."""
    out: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and (name := _dotted(node.func)):
            head, _, rest = name.partition(".")
            resolved = aliases.get(head, head)
            out.add(f"{resolved}.{rest}" if rest else resolved)
    return out


def _imported_modules(tree: ast.AST) -> list[str]:
    out: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            out += [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
            out.append(node.module)
    return out


def _forbidden(modules: Iterable[str]) -> list[str]:
    return sorted({m for m in modules if m.split(".")[0] in FORBIDDEN_MODULES})


def _bindings(nodes: Iterable[ast.stmt]) -> set[tuple[str, str, str | None]]:
    """What import statements bind, as (module, name, asname)."""
    out: set[tuple[str, str, str | None]] = set()
    for node in nodes:
        if isinstance(node, ast.ImportFrom):
            module = "." * node.level + (node.module or "")
            out |= {(module, a.name, a.asname) for a in node.names}
        elif isinstance(node, ast.Import):
            out |= {("", a.name, a.asname) for a in node.names}
    return out


# -- test files ---------------------------------------------------------------


@dataclass
class TestFileCheck:
    __test__ = False  # not a pytest class

    code: str  # with any missing imports added
    test_names: list[str]  # ``test_x`` or ``TestY::test_x``
    workload_test: str | None
    problems: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.problems


def _is_workload(decorator: ast.expr) -> bool:
    target = decorator.func if isinstance(decorator, ast.Call) else decorator
    name = _dotted(target) or ""
    return name == WORKLOAD_MARK or name.endswith("." + WORKLOAD_MARK)


def _tests(tree: ast.Module) -> list[tuple[str, FuncNode]]:
    """Tests as pytest collects them with default settings."""
    out: list[tuple[str, FuncNode]] = []
    for node in tree.body:
        if isinstance(node, FuncNode) and node.name.startswith("test"):
            out.append((node.name, node))
        elif isinstance(node, ast.ClassDef) and node.name.startswith("Test"):
            out += [
                (f"{node.name}::{item.name}", item)
                for item in node.body
                if isinstance(item, FuncNode) and item.name.startswith("test")
            ]
    return out


def _insert_imports(code: str, tree: ast.Module, lines: Sequence[str]) -> str:
    """Add ``lines`` after the leading docstring and imports."""
    after = 0
    for i, node in enumerate(tree.body):
        is_doc = i == 0 and isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant)
        if not (is_doc or isinstance(node, ast.Import | ast.ImportFrom)):
            break
        after = node.end_lineno or node.lineno
    src = code.splitlines(keepends=True)
    return "".join([*src[:after], *(f"{line}\n" for line in lines), *src[after:]])


def _import_line_bindings(fn: DiscoveredFunction) -> set[tuple[str, str, str | None]]:
    try:
        return _bindings(ast.parse(fn.import_line.strip()).body)
    except SyntaxError:
        return set()


def _shadowed(tree: ast.Module, fn: DiscoveredFunction) -> list[str]:
    """Names the import line brings in (the class, for a method) that the file rebinds."""
    names = {asname or name.split(".")[0] for _, name, asname in _import_line_bindings(fn)}
    names = names or {fn.name}
    bound: set[str] = set()
    for node in tree.body:
        if isinstance(node, FuncNode | ast.ClassDef):
            bound.add(node.name)
        elif isinstance(node, ast.Assign | ast.AnnAssign | ast.AugAssign):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            bound |= {n.id for t in targets for n in ast.walk(t) if isinstance(n, ast.Name)}
    return sorted(names & bound)


def _missing_imports(tree: ast.Module, fn: DiscoveredFunction) -> list[str]:
    top = [n for n in tree.body if isinstance(n, ast.Import | ast.ImportFrom)]
    have = _bindings(top)
    used = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
    missing = []
    if "pytest" in used and ("", "pytest", None) not in have:
        missing.append("import pytest")
    needed = _import_line_bindings(fn)
    if needed and not needed <= have:
        missing.append(fn.import_line.strip())
    return missing


def validate_test_file(code: str, fn: DiscoveredFunction) -> TestFileCheck:
    """Check a generated test file and add the imports it forgot."""
    code = strip_fences(code).strip("\n") + "\n"
    try:
        tree = ast.parse(code)
    except SyntaxError as e:
        return TestFileCheck(code, [], None, [f"syntax error on line {e.lineno}: {e.msg}"])

    if missing := _missing_imports(tree, fn):
        code = _insert_imports(code, tree, missing)
        tree = ast.parse(code)

    problems: list[str] = []
    tests = _tests(tree)
    names = [name for name, _ in tests]
    workloads = [name for name, node in tests if any(map(_is_workload, node.decorator_list))]
    if len(tests) < MIN_TESTS:
        problems.append(f"only {len(tests)} tests; write at least {MIN_TESTS}")
    if not workloads:
        problems.append("no test is marked @pytest.mark.nz_workload; mark exactly one")
    elif len(workloads) > 1:
        problems.append(
            f"{len(workloads)} tests are marked @pytest.mark.nz_workload "
            f"({', '.join(workloads)}); mark exactly one"
        )
    if shadowed := _shadowed(tree, fn):
        problems.append(
            f"the file defines its own {', '.join(shadowed)}; import the real one instead"
        )
    used = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
    used |= {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)}
    if fn.name not in used:
        problems.append(f"no test uses {fn.name}")
    if bad := _forbidden(_imported_modules(tree)):
        problems.append(f"imports {', '.join(bad)}; tests must not touch the network or OS")
    aliases = _aliases(tree)
    if bad := sorted(_calls(tree, aliases) & FORBIDDEN_CALLS):
        problems.append(f"calls {', '.join(bad)}; tests must not do I/O or run code")
    # aliases catch ``from unittest import mock``, whose module is just ``unittest``, and
    # dotted calls catch ``import unittest`` then ``unittest.mock.patch(...)``
    dotted_calls = {c for c in _calls(tree, aliases) if "." in c}
    imported = {*_imported_modules(tree), *aliases.values(), *dotted_calls}
    mocks = any(m == mm or m.startswith(mm + ".") for m in imported for mm in MOCK_MODULES)
    if mocks or "monkeypatch" in used or "mocker" in used:
        problems.append("uses mocking or monkeypatch; call the real function")
    return TestFileCheck(code, names, workloads[0] if len(workloads) == 1 else None, problems)


# -- candidates ---------------------------------------------------------------


@dataclass
class CandidateCheck:
    static: StaticCheck
    import_modules: list[str]  # modules the candidate needs, from new_imports and its body
    code: str = ""  # the normalized candidate: fences stripped, dedented


def _without_docstrings(node: FuncNode) -> str:
    node = copy.deepcopy(node)
    for n in ast.walk(node):
        body = getattr(n, "body", None)
        if (
            isinstance(n, FuncNode | ast.ClassDef)
            and body
            and isinstance(body[0], ast.Expr)
            and isinstance(body[0].value, ast.Constant)
            and isinstance(body[0].value.value, str)
        ):
            n.body = body[1:] or [ast.Pass()]
    return ast.dump(node)


def _signature(node: FuncNode) -> str:
    returns = f" -> {ast.unparse(node.returns)}" if node.returns else ""
    return f"({ast.unparse(node.args)}){returns}"


def _bound_names(tree: ast.AST) -> set[str]:
    """Every name bound anywhere in ``tree``: generous, so the undefined-name check errs quiet."""
    out: set[str] = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Store | ast.Del):
            out.add(n.id)
        elif isinstance(n, FuncNode | ast.ClassDef):
            out.add(n.name)
        elif isinstance(n, ast.arg):
            out.add(n.arg)
        elif isinstance(n, ast.alias):
            out.add(n.asname or n.name.split(".")[0])
        elif isinstance(n, ast.ExceptHandler | ast.MatchAs | ast.MatchStar) and n.name:
            out.add(n.name)
        elif isinstance(n, ast.MatchMapping) and n.rest:
            out.add(n.rest)
        elif isinstance(n, ast.Global | ast.Nonlocal):
            out.update(n.names)
        elif isinstance(n, ast.TypeVar | ast.ParamSpec | ast.TypeVarTuple):
            out.add(n.name)  # ``def f[T](...)``
    return out


def _undefined_names(node: FuncNode, module_tree: ast.Module, extra: set[str]) -> list[str]:
    if any(
        isinstance(n, ast.ImportFrom) and any(a.name == "*" for a in n.names)
        for n in ast.walk(module_tree)
    ):
        return []  # a star import could define anything
    known = _bound_names(node) | _bound_names(module_tree) | extra
    known |= set(dir(builtins)) | _MODULE_DUNDERS
    loaded = {
        n.id
        for stmt in node.body
        for n in ast.walk(stmt)
        if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load)
    }
    return sorted(loaded - known)


ImportNode = ast.Import | ast.ImportFrom


def _parse_new_imports(new_imports: Sequence[str]) -> tuple[list[ImportNode], list[str]]:
    nodes: list[ImportNode] = []
    problems: list[str] = []
    for line in new_imports:
        try:
            body = ast.parse(line.strip()).body
        except SyntaxError:
            body = []
        node = body[0] if len(body) == 1 else None
        if isinstance(node, ast.Import) or (
            isinstance(node, ast.ImportFrom)
            and not node.level
            and all(a.name != "*" for a in node.names)
        ):
            nodes.append(node)
        else:
            problems.append(f"new_imports entry {line!r} is not one absolute import statement")
    return nodes, problems


def check_candidate(
    original_source: str,
    candidate_code: str,
    fn: DiscoveredFunction,
    *,
    new_imports: Sequence[str] = (),
    module_source: str | None = None,
) -> CandidateCheck:
    """Is ``candidate_code`` a safe drop-in for ``fn``?

    ``original_source`` is the module or just the function. With
    ``module_source``, imports must be stdlib or already in the module, and
    every name the candidate reads must be bound somewhere.
    """
    code = textwrap.dedent(strip_fences(candidate_code)).strip("\n") + "\n"
    import_nodes, problems = _parse_new_imports(new_imports)
    modules = _imported_modules(ast.Module(body=list(import_nodes), type_ignores=[]))
    module_tree = _parse_or_none(module_source)
    module_aliases = _aliases(module_tree) if module_tree is not None else {}
    try:
        tree = ast.parse(code)
    except SyntaxError as e:
        problems.append(f"syntax error on line {e.lineno}: {e.msg}")
    else:
        modules += _imported_modules(tree)
        problems += _candidate_problems(original_source, tree, fn, modules, module_aliases)
        node = _candidate_node(tree, fn)
        if module_tree is not None and node is not None:
            problems += _module_problems(node, module_tree, modules, import_nodes)
    return CandidateCheck(
        static=StaticCheck(ok=not problems, problems=problems),
        import_modules=list(dict.fromkeys(modules)),
        code=code,
    )


def _parse_or_none(source: str | None) -> ast.Module | None:
    if source is None:
        return None
    try:
        return ast.parse(source)
    except SyntaxError:
        return None


def _risky(calls: set[str]) -> set[str]:
    return {c for c in calls if c in IO_CALLS or c.split(".")[0] in FORBIDDEN_MODULES}


def _candidate_node(tree: ast.Module, fn: DiscoveredFunction) -> FuncNode | None:
    defs = [n for n in tree.body if isinstance(n, FuncNode)]
    return next((d for d in defs if d.name == fn.name), defs[0] if defs else None)


def _candidate_problems(
    original_source: str,
    tree: ast.Module,
    fn: DiscoveredFunction,
    modules: list[str],
    module_aliases: dict[str, str],
) -> list[str]:
    try:
        original_tree = ast.parse(textwrap.dedent(original_source))
    except SyntaxError:
        return [f"could not parse the original {fn.qualname} to compare against"]
    original = find_function_node(original_tree, fn.qualname) or next(
        (n for n in ast.walk(original_tree) if isinstance(n, FuncNode) and n.name == fn.name),
        None,
    )
    if original is None:
        return [f"could not find the original {fn.qualname} to compare against"]

    problems: list[str] = []
    if len(tree.body) != 1 or not isinstance(tree.body[0], FuncNode):
        found = ", ".join(type(n).__name__ for n in tree.body) or "nothing"
        problems.append(
            "code must be exactly one function definition and nothing else at top level "
            f"(found {found}); put helpers inside the function"
        )
    node = _candidate_node(tree, fn)
    if node is None:
        return problems
    if node.name != fn.name:
        problems.append(f"renamed {fn.name} to {node.name}")
    if isinstance(node, ast.AsyncFunctionDef) != isinstance(original, ast.AsyncFunctionDef):
        problems.append("changed whether the function is async")
    if _signature(node) != _signature(original):
        problems.append(f"changed the signature from {_signature(original)} to {_signature(node)}")
    if list(map(ast.dump, node.decorator_list)) != list(map(ast.dump, original.decorator_list)):
        problems.append("changed the decorators; keep them exactly")
    if any(isinstance(n, ast.Global | ast.Nonlocal) for n in ast.walk(node)):
        problems.append("uses global or nonlocal; keep all state local")
    # the module's own imports resolve too: ``subprocess.run`` is new I/O even when the
    # module already imports subprocess
    calls = _calls(node, {**module_aliases, **_aliases(tree)})
    before = _calls(original, {**module_aliases, **_aliases(original_tree)})
    if new_io := sorted(_risky(calls) - before):
        problems.append(f"calls {', '.join(new_io)}, which the original does not")
    if bad := _forbidden(modules):
        problems.append(f"imports {', '.join(bad)}; no I/O, processes or network")
    if _without_docstrings(node) == _without_docstrings(original):
        problems.append("the code is identical to the original")
    return problems


def _module_problems(
    node: FuncNode, module_tree: ast.Module, modules: list[str], import_nodes: list[ImportNode]
) -> list[str]:
    """Imports and names checked against the module the candidate goes into."""
    problems: list[str] = []
    allowed = {m.split(".")[0] for m in _imported_modules(module_tree)} | set(
        sys.stdlib_module_names
    )
    if outside := sorted({m for m in modules if m.split(".")[0] not in allowed}):
        problems.append(
            f"imports {', '.join(outside)}, which is neither stdlib nor imported by the module"
        )
    extra = {a.asname or a.name.split(".")[0] for n in import_nodes for a in n.names}
    if undefined := _undefined_names(node, module_tree, extra):
        problems.append(f"uses undefined names {', '.join(undefined)}; import them via new_imports")
    return problems


def candidate_diff(original_source: str, candidate_code: str, path: str) -> str:
    """A ``git diff``-style patch, 3 lines of context; empty when nothing changed."""
    before = original_source if original_source.endswith("\n") else original_source + "\n"
    after = candidate_code if candidate_code.endswith("\n") else candidate_code + "\n"
    body = "".join(
        difflib.unified_diff(
            before.splitlines(keepends=True),
            after.splitlines(keepends=True),
            fromfile=f"a/{path}",
            tofile=f"b/{path}",
            n=3,
        )
    )
    return f"diff --git a/{path} b/{path}\n{body}" if body else ""
