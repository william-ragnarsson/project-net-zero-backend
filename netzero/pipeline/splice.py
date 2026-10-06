"""Splice rewritten functions back into source files.

Salvaged from the original ``src/convertor/inplace_rewriter.py`` and
generalised: qualnames of any depth (``Outer.Inner.method``), decorators are
part of the replaced span, and replacements are applied bottom-to-top so
earlier line numbers stay valid.
"""

from __future__ import annotations

import ast
import textwrap
from dataclasses import dataclass
from pathlib import Path

FuncNode = ast.FunctionDef | ast.AsyncFunctionDef


class SpliceError(Exception):
    pass


@dataclass(frozen=True)
class Span:
    """1-based inclusive line span of a function, decorators included."""

    start: int
    end: int
    indent: int


def find_function_node(tree: ast.Module, qualname: str) -> FuncNode | None:
    """Locate a function or method by dotted qualname (any nesting depth)."""
    parts = qualname.split(".")
    scope: list[ast.stmt] = tree.body
    for i, name in enumerate(parts):
        last = i == len(parts) - 1
        found: ast.AST | None = None
        for node in scope:
            if (
                last
                and isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                and node.name == name
            ):
                found = node
            elif (
                not last
                and isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
                and node.name == name
            ):
                found = node
        if found is None:
            return None
        if last:
            return found  # type: ignore[return-value]
        scope = found.body  # type: ignore[attr-defined]
    return None


def function_span(source: str, qualname: str) -> Span:
    tree = ast.parse(source)
    node = find_function_node(tree, qualname)
    if node is None:
        raise SpliceError(f"function {qualname!r} not found")
    start = min([node.lineno, *(d.lineno for d in node.decorator_list)])
    end = node.end_lineno or node.lineno
    lines = source.splitlines()
    first = lines[start - 1]
    indent = len(first) - len(first.lstrip())
    return Span(start=start, end=end, indent=indent)


def function_source(source: str, qualname: str) -> str:
    """Exact source of a function including decorators and comments, dedented."""
    span = function_span(source, qualname)
    lines = source.splitlines(keepends=True)
    return textwrap.dedent("".join(lines[span.start - 1 : span.end]))


def reindent(code: str, indent: int) -> str:
    """Dedent ``code`` then indent every non-blank line by ``indent`` spaces."""
    dedented = textwrap.dedent(code.expandtabs(4)).strip("\n") + "\n"
    if indent == 0:
        return dedented
    return textwrap.indent(dedented, " " * indent)


def splice_source(source: str, replacements: dict[str, str]) -> str:
    """Replace each ``qualname -> new code`` in ``source`` and return the result.

    The result must parse; otherwise ``SpliceError`` is raised.
    """
    spans: list[tuple[Span, str]] = []
    for qualname, code in replacements.items():
        spans.append((function_span(source, qualname), code))
    spans.sort(key=lambda s: s[0].start, reverse=True)
    for (a, _), (b, _) in zip(spans, spans[1:], strict=False):
        if b.end >= a.start:
            raise SpliceError("overlapping replacements")

    lines = source.splitlines(keepends=True)
    if lines and not lines[-1].endswith("\n"):
        lines[-1] += "\n"
    for span, code in spans:
        new_lines = reindent(code, span.indent).splitlines(keepends=True)
        lines[span.start - 1 : span.end] = new_lines
    out = "".join(lines)
    try:
        ast.parse(out)
    except SyntaxError as e:
        raise SpliceError(f"spliced file does not parse: {e}") from e
    return out


def splice_file(path: Path, replacements: dict[str, str]) -> None:
    text = path.read_text(encoding="utf-8")
    path.write_text(splice_source(text, replacements), encoding="utf-8")
