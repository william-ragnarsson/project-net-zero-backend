"""Find every function in a repo, with exact source, import line and static features.

Salvaged from the original ``src/parser/graph_parser.py``: same directory and
test-file rules, same recursive walk, but functions keep their exact source
slice (decorators and comments included) instead of ``ast.unparse``, ids use
``module:qualname``, and modules are resolved against their import root
(walk up while ``__init__.py`` exists).
"""

from __future__ import annotations

import ast
import textwrap
from dataclasses import dataclass, field
from pathlib import Path

from netzero.events import FunctionInfo, FunctionKind

EXCLUDED_DIRS = {
    ".git",
    ".hg",
    ".svn",
    ".venv",
    "venv",
    "env",
    "__pycache__",
    "build",
    "dist",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    "site-packages",
    "node_modules",
    ".tox",
    ".nox",
    ".eggs",
    ".netzero-cassettes",
    "docs",
    "examples",
    "benchmarks",
}

TEST_DIR_NAMES = {"tests", "test", "spec", "specs", "testing"}
TEST_FILE_PREFIXES = ("test_", "spec_")
TEST_FILE_SUFFIXES = ("_test.py", "_spec.py")
SKIP_FILE_NAMES = {"setup.py", "conftest.py", "noxfile.py", "manage.py", "__main__.py"}

IO_MODULES = {
    "requests",
    "urllib",
    "urllib3",
    "httpx",
    "aiohttp",
    "socket",
    "subprocess",
    "shutil",
    "sqlite3",
    "smtplib",
    "ftplib",
    "http",
    "boto3",
    "glob",
    "tempfile",
}
IO_BUILTINS = {"open", "input", "print", "exec", "eval", "breakpoint", "compile"}
IO_ATTRS = {
    "read_text",
    "write_text",
    "read_bytes",
    "write_bytes",
    "urlopen",
    "mkdir",
    "unlink",
    "rmdir",
    "makedirs",
    "system",
    "popen",
    "listdir",
}
OS_IO = {"remove", "rename", "walk", "getenv", "getcwd", "stat", "scandir", "chdir"}
NONDET_CALLS = {
    "time.time",
    "time.time_ns",
    "time.perf_counter",
    "time.monotonic",
    "os.urandom",
    "uuid.uuid1",
    "uuid.uuid4",
    "datetime.datetime.now",
    "datetime.datetime.utcnow",
    "datetime.datetime.today",
    "datetime.date.today",
    "datetime.now",
    "datetime.today",
    "date.today",
}
NONDET_MODULES = {"random", "secrets", "numpy.random"}


@dataclass
class StaticFeatures:
    loc: int
    n_args: int
    n_loops: int = 0
    max_loop_depth: int = 0
    n_comprehensions: int = 0
    recursive: bool = False
    is_async: bool = False
    is_generator: bool = False
    writes_global: bool = False
    returns_value: bool = False
    io_calls: list[str] = field(default_factory=list)
    nondet_calls: list[str] = field(default_factory=list)
    anti_patterns: list[str] = field(default_factory=list)
    decorators: list[str] = field(default_factory=list)


@dataclass
class DiscoveredFunction:
    function_id: str  # "pkg.mod:Qual.name"
    module: str
    qualname: str
    name: str
    kind: FunctionKind
    file: str  # repo-relative POSIX path
    line: int  # first line, decorators included
    end_line: int
    def_line: int
    source: str  # exact, dedented
    import_line: str
    call_hint: str
    import_root: str  # repo-relative, "." for the repo root
    class_path: list[str]  # enclosing classes, outermost first
    nested_in_function: bool
    features: StaticFeatures

    def info(self) -> FunctionInfo:
        return FunctionInfo(
            function_id=self.function_id,
            module=self.module,
            qualname=self.qualname,
            kind=self.kind,
            file=self.file,
            line=self.line,
            end_line=self.end_line,
            loc=self.features.loc,
            import_line=self.import_line,
            call_hint=self.call_hint,
            source=self.source,
        )


@dataclass
class DiscoveryResult:
    functions: list[DiscoveredFunction]
    source_files: list[str]
    test_files: list[str]
    import_roots: list[str]
    parse_errors: dict[str, str]


# ---------------------------------------------------------------------------
# File discovery
# ---------------------------------------------------------------------------


def _is_excluded(rel: Path) -> bool:
    return any(part in EXCLUDED_DIRS or part.startswith(".") for part in rel.parts[:-1])


def is_test_file(rel: Path) -> bool:
    lower_parts = {p.lower() for p in rel.parts[:-1]}
    if lower_parts & TEST_DIR_NAMES:
        return True
    name = rel.name.lower()
    return name.startswith(TEST_FILE_PREFIXES) or name.endswith(TEST_FILE_SUFFIXES)


def discover_source_files(root: Path) -> list[Path]:
    out = []
    for path in sorted(root.rglob("*.py")):
        rel = path.relative_to(root)
        if _is_excluded(rel) or is_test_file(rel) or rel.name in SKIP_FILE_NAMES:
            continue
        out.append(path)
    return out


def discover_test_files(root: Path) -> list[Path]:
    out = []
    for path in sorted(root.rglob("*.py")):
        rel = path.relative_to(root)
        if not _is_excluded(rel) and is_test_file(rel):
            out.append(path)
    return out


def import_root_of(root: Path, file: Path) -> Path:
    """Walk up from the file's directory while ``__init__.py`` exists."""
    d = file.parent
    while d != root and (d / "__init__.py").exists():
        d = d.parent
    return d


def module_name(import_root: Path, file: Path) -> str:
    rel = file.relative_to(import_root).with_suffix("")
    parts = [p.replace("-", "_") for p in rel.parts]
    if parts and parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


# ---------------------------------------------------------------------------
# AST
# ---------------------------------------------------------------------------


def _dotted(node: ast.AST) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        base = _dotted(node.value)
        return f"{base}.{node.attr}" if base else None
    if isinstance(node, ast.Call):
        return _dotted(node.func)
    return None


def _import_aliases(tree: ast.Module) -> dict[str, str]:
    """Local name -> fully-qualified dotted name, from module-level imports."""
    aliases: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                aliases[a.asname or a.name.split(".")[0]] = (
                    a.name if a.asname else a.name.split(".")[0]
                )
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            for a in node.names:
                aliases[a.asname or a.name] = f"{node.module}.{a.name}"
    return aliases


def _resolve(name: str, aliases: dict[str, str]) -> str:
    head, _, rest = name.partition(".")
    if head in aliases:
        return aliases[head] + ("." + rest if rest else "")
    return name


class _FeatureVisitor(ast.NodeVisitor):
    def __init__(self, fn: ast.FunctionDef | ast.AsyncFunctionDef, aliases: dict[str, str]):
        self.fn = fn
        self.aliases = aliases
        self.depth = 0
        self.f = StaticFeatures(loc=0, n_args=0)
        self._str_vars: set[str] = set()
        self._list_vars: set[str] = set()
        self._anti: dict[str, None] = {}

    def anti(self, msg: str) -> None:
        self._anti.setdefault(msg, None)

    # do not descend into nested defs/classes: they are separate units
    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        if node is self.fn:
            self.generic_visit(node)

    visit_AsyncFunctionDef = visit_FunctionDef  # type: ignore[assignment]

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        return

    def visit_Lambda(self, node: ast.Lambda) -> None:
        return

    def _loop(self, node: ast.AST) -> None:
        self.f.n_loops += 1
        self.depth += 1
        self.f.max_loop_depth = max(self.f.max_loop_depth, self.depth)
        if self.depth >= 2:
            self.anti("nested loops")
        self.generic_visit(node)
        self.depth -= 1

    def visit_For(self, node: ast.For) -> None:
        it = node.iter
        if (
            isinstance(it, ast.Call)
            and _dotted(it.func) == "range"
            and len(it.args) == 1
            and isinstance(it.args[0], ast.Call)
            and _dotted(it.args[0].func) == "len"
        ):
            self.anti("index loop over range(len(...))")
        self._loop(node)

    visit_AsyncFor = visit_For  # type: ignore[assignment]

    def visit_While(self, node: ast.While) -> None:
        self._loop(node)

    def _comp(self, node: ast.AST) -> None:
        self.f.n_comprehensions += 1
        self.depth += 1
        self.f.max_loop_depth = max(self.f.max_loop_depth, self.depth)
        if self.depth >= 2:
            self.anti("nested loops")
        self.generic_visit(node)
        self.depth -= 1

    visit_ListComp = visit_SetComp = visit_DictComp = visit_GeneratorExp = _comp  # type: ignore[assignment]

    def visit_Assign(self, node: ast.Assign) -> None:
        for t in node.targets:
            if isinstance(t, ast.Name):
                v = node.value
                if isinstance(v, ast.Constant) and isinstance(v.value, str):
                    self._str_vars.add(t.id)
                if isinstance(v, (ast.List, ast.ListComp)) or (
                    isinstance(v, ast.Call) and _dotted(v.func) == "list"
                ):
                    self._list_vars.add(t.id)
            if self.depth and isinstance(t, ast.Subscript) and isinstance(node.value, ast.BinOp):
                v = node.value
                if (
                    isinstance(v.left, ast.Call)
                    and _dotted(v.left.func)
                    and str(_dotted(v.left.func)).endswith(".get")
                ):
                    self.anti("manual counting with dict.get")
        self.generic_visit(node)

    def visit_AugAssign(self, node: ast.AugAssign) -> None:
        if self.depth and isinstance(node.op, ast.Add) and isinstance(node.target, ast.Name):
            if node.target.id in self._str_vars:
                self.anti("string concatenation in a loop")
            elif node.target.id in self._list_vars:
                self.anti("list concatenation in a loop")
        self.generic_visit(node)

    def visit_Compare(self, node: ast.Compare) -> None:
        if self.depth and any(isinstance(op, (ast.In, ast.NotIn)) for op in node.ops):
            for comp in node.comparators:
                if isinstance(comp, ast.Name) and comp.id in self._list_vars:
                    self.anti("membership test on a list inside a loop")
                elif isinstance(comp, (ast.List, ast.Tuple)) and len(getattr(comp, "elts", [])) > 4:
                    self.anti("membership test on a literal list inside a loop")
        self.generic_visit(node)

    def visit_Subscript(self, node: ast.Subscript) -> None:
        if self.depth and isinstance(node.slice, ast.Slice) and isinstance(node.ctx, ast.Load):
            self.anti("slicing inside a loop")
        self.generic_visit(node)

    def visit_Global(self, node: ast.Global) -> None:
        self.f.writes_global = True

    def visit_Nonlocal(self, node: ast.Nonlocal) -> None:
        self.f.writes_global = True

    def visit_Yield(self, node: ast.Yield) -> None:
        self.f.is_generator = True
        self.generic_visit(node)

    def visit_YieldFrom(self, node: ast.YieldFrom) -> None:
        self.f.is_generator = True
        self.generic_visit(node)

    def visit_Await(self, node: ast.Await) -> None:
        self.f.is_async = True
        self.generic_visit(node)

    def visit_Return(self, node: ast.Return) -> None:
        if node.value is not None:
            self.f.returns_value = True
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:
        raw = _dotted(node.func)
        if raw:
            name = _resolve(raw, self.aliases)
            head = name.split(".")[0]
            attr = name.rsplit(".", 1)[-1]
            if (
                raw == self.fn.name
                or raw.endswith(f"self.{self.fn.name}")
                or raw.endswith(f"cls.{self.fn.name}")
            ):
                self.f.recursive = True
            if (
                head in IO_MODULES
                or (raw in IO_BUILTINS)
                or (
                    name.startswith("os.")
                    and (attr in IO_ATTRS | OS_IO or name.startswith("os.path."))
                )
                or (attr in IO_ATTRS and "." in raw and not raw.startswith("self."))
                or name.startswith("time.sleep")
                or name.startswith(("pickle.load", "pickle.dump", "json.load", "json.dump"))
                and not name.endswith(("loads", "dumps"))
            ):
                self.f.io_calls.append(name)
            if name in NONDET_CALLS or head in NONDET_MODULES or name.startswith("numpy.random"):
                self.f.nondet_calls.append(name)
            if self.depth:
                if attr in {"sort", "sorted"} or raw == "sorted":
                    self.anti("sorting inside a loop")
                if attr in {"index", "count"} and "." in raw:
                    self.anti("linear search inside a loop")
                if (
                    attr == "insert"
                    and node.args
                    and isinstance(node.args[0], ast.Constant)
                    and node.args[0].value == 0
                ):
                    self.anti("list.insert(0, ...) inside a loop")
                if (
                    attr == "pop"
                    and node.args
                    and isinstance(node.args[0], ast.Constant)
                    and node.args[0].value == 0
                ):
                    self.anti("list.pop(0) inside a loop")
                if (
                    raw in {"sum", "max", "min", "len"}
                    and node.args
                    and isinstance(node.args[0], ast.Subscript)
                ):
                    self.anti("re-aggregating a slice inside a loop")
        self.generic_visit(node)

    def visit_If(self, node: ast.If) -> None:
        # if k in d: d[k] += 1 else: d[k] = 1
        t = node.test
        if (
            self.depth
            and isinstance(t, ast.Compare)
            and len(t.ops) == 1
            and isinstance(t.ops[0], (ast.In, ast.NotIn))
            and node.orelse
            and any(
                isinstance(s, ast.AugAssign) and isinstance(s.target, ast.Subscript)
                for s in node.body + node.orelse
            )
        ):
            self.anti("manual counting with if/else on a dict")
        self.generic_visit(node)

    def result(self) -> StaticFeatures:
        if self.f.recursive and not self.f.decorators:
            self.anti("recursion without memoisation")
        self.f.anti_patterns = list(self._anti)
        self.f.io_calls = sorted(set(self.f.io_calls))
        self.f.nondet_calls = sorted(set(self.f.nondet_calls))
        return self.f


def static_features(
    fn: ast.FunctionDef | ast.AsyncFunctionDef, aliases: dict[str, str], loc: int
) -> StaticFeatures:
    v = _FeatureVisitor(fn, aliases)
    v.f.loc = loc
    a = fn.args
    v.f.n_args = (
        len(a.posonlyargs) + len(a.args) + len(a.kwonlyargs) + bool(a.vararg) + bool(a.kwarg)
    )
    v.f.is_async = isinstance(fn, ast.AsyncFunctionDef)
    v.f.decorators = [_dotted(d) or ast.unparse(d) for d in fn.decorator_list]
    v.visit(fn)
    return v.result()


def _kind(fn: ast.FunctionDef | ast.AsyncFunctionDef, in_class: bool) -> FunctionKind:
    if not in_class:
        return "function"
    names = {(_dotted(d) or "").rsplit(".", 1)[-1] for d in fn.decorator_list}
    if "staticmethod" in names:
        return "staticmethod"
    if "classmethod" in names:
        return "classmethod"
    return "method"


def _call_hint(
    kind: FunctionKind, class_path: list[str], fn: ast.FunctionDef | ast.AsyncFunctionDef
) -> str:
    params = [a.arg for a in fn.args.posonlyargs + fn.args.args]
    if kind in ("method", "classmethod") and params:
        params = params[1:]
    params += [f"{a.arg}=..." for a in fn.args.kwonlyargs]
    arglist = ", ".join(params)
    if kind == "function":
        return f"{fn.name}({arglist})"
    owner = ".".join(class_path)
    if kind in ("staticmethod", "classmethod"):
        return f"{owner}.{fn.name}({arglist})"
    return f"{owner}(...).{fn.name}({arglist})"


def parse_module(root: Path, file: Path) -> tuple[list[DiscoveredFunction], str | None]:
    """Return the functions defined in ``file`` (or an error message)."""
    try:
        text = file.read_text(encoding="utf-8")
        tree = ast.parse(text, filename=str(file))
    except (SyntaxError, OSError, UnicodeDecodeError) as e:
        return [], f"{type(e).__name__}: {e}"

    lines = text.splitlines(keepends=True)
    iroot = import_root_of(root, file)
    module = module_name(iroot, file)
    rel_file = file.relative_to(root).as_posix()
    rel_root = iroot.relative_to(root).as_posix() or "."
    aliases = _import_aliases(tree)
    out: list[DiscoveredFunction] = []

    def walk(node: ast.AST, class_path: list[str], qual: list[str], in_func: bool) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.ClassDef):
                walk(child, [*class_path, child.name], [*qual, child.name], in_func)
            elif isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                qualname = ".".join([*qual, child.name])
                in_class = isinstance(node, ast.ClassDef)
                kind = _kind(child, in_class)
                start = min([child.lineno, *(d.lineno for d in child.decorator_list)])
                end = child.end_lineno or child.lineno
                source = textwrap.dedent("".join(lines[start - 1 : end]))
                loc = sum(
                    1 for ln in source.splitlines() if ln.strip() and not ln.strip().startswith("#")
                )
                top_owner = class_path[0] if class_path else child.name
                out.append(
                    DiscoveredFunction(
                        function_id=f"{module}:{qualname}",
                        module=module,
                        qualname=qualname,
                        name=child.name,
                        kind=kind,
                        file=rel_file,
                        line=start,
                        end_line=end,
                        def_line=child.lineno,
                        source=source,
                        import_line=f"from {module} import {top_owner}"
                        if module
                        else f"import {top_owner}",
                        call_hint=_call_hint(kind, class_path, child),
                        import_root=rel_root,
                        class_path=list(class_path),
                        nested_in_function=in_func,
                        features=static_features(child, aliases, loc),
                    )
                )
                walk(child, class_path, [*qual, child.name], True)

    walk(tree, [], [], False)
    return out, None


def discover(root: Path) -> DiscoveryResult:
    root = root.resolve()
    sources = discover_source_files(root)
    tests = discover_test_files(root)
    functions: list[DiscoveredFunction] = []
    errors: dict[str, str] = {}
    roots: dict[str, None] = {}
    for f in sources:
        found, err = parse_module(root, f)
        rel = f.relative_to(root).as_posix()
        if err:
            errors[rel] = err
            continue
        roots.setdefault(import_root_of(root, f).relative_to(root).as_posix() or ".", None)
        functions.extend(found)
    return DiscoveryResult(
        functions=functions,
        source_files=[f.relative_to(root).as_posix() for f in sources],
        test_files=[f.relative_to(root).as_posix() for f in tests],
        import_roots=list(roots),
        parse_errors=errors,
    )


def find_requirements(root: Path) -> list[Path]:
    """Dependency files, most specific first."""
    found = sorted(p for p in root.glob("requirements*.txt") if p.is_file())
    for name in ("pyproject.toml", "setup.py", "setup.cfg"):
        if (root / name).is_file():
            found.append(root / name)
    return found
