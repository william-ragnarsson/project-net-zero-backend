"""Recorded LLM responses, so demo runs and tests replay without an API key.

One JSON file per call, keyed by what the pipeline asked for rather than by the
request bytes: a reworded prompt still replays (the stored ``request_hash`` only
warns) and a file is easy to write by hand. Only ``output`` is required::

    {"version": 1, "key": {...}, "model": "claude-haiku-4-5", "request_hash": "...",
     "output": {...}, "usage": {"input_tokens": 0, ...}, "stop_reason": "end_turn"}
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from netzero import paths
from netzero.errors import LlmError
from netzero.events import CandidateId, LlmStage
from netzero.llm.pricing import TOKEN_FIELDS

FORMAT_VERSION = 1
_UNSAFE = re.compile(r"[^A-Za-z0-9._-]")


@dataclass(frozen=True)
class CassetteKey:
    stage: LlmStage
    function_id: str | None = None
    candidate: CandidateId | None = None
    attempt: int = 0

    def relpath(self) -> Path:
        """``<stage>/<fslug>/<candidate or _>/<attempt>.json``"""
        return Path(
            self.stage, fslug(self.function_id), self.candidate or "_", f"{self.attempt}.json"
        )


@dataclass
class CassetteEntry:
    output: dict[str, Any]
    model: str = ""
    request_hash: str | None = None
    usage: dict[str, int] | None = None
    stop_reason: str | None = None


def fslug(function_id: str | None) -> str:
    """A directory name for a function id: ``pkg.mod:Cls.f`` -> ``pkg.mod__Cls.f``."""
    if function_id is None:
        return "_"
    slug = _UNSAFE.sub("_", function_id.replace(":", "__"))
    return slug if slug.strip(".") else "_"  # never "", "." or ".."


def request_hash(model: str, system: str, messages: list[dict[str, Any]], schema: Any) -> str:
    """sha256 of the canonical JSON of what decides the response."""
    blob = json.dumps(
        {"model": model, "system": system, "messages": messages, "schema": schema},
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def _is_count(value: object) -> bool:
    """A hand-written token count: absent, or an int that is not negative (``true`` is not)."""
    return value is None or (type(value) is int and value >= 0)


def _shown(path: Path) -> str:
    """Repo-relative when possible, so messages do not leak the checkout location."""
    try:
        return path.relative_to(paths.ROOT).as_posix()
    except ValueError:
        return path.as_posix()


class CassetteStore:
    def __init__(self, root: Path | None = None):
        self.root = Path(root) if root is not None else paths.CASSETTES_DIR

    def path(self, key: CassetteKey) -> Path:
        return self.root / key.relpath()

    def load(self, key: CassetteKey) -> CassetteEntry:
        path = self.path(key)
        if not path.is_file():
            raise LlmError(
                f"no cassette for {key.stage} {key.function_id or ''}".rstrip()
                + f": expected {_shown(path)}",
                kind="cassette_miss",
            )
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, ValueError) as e:
            raise LlmError(f"unreadable cassette {_shown(path)}: {e}") from e
        if not isinstance(raw, dict) or not isinstance(raw.get("output"), dict):
            raise LlmError(f"cassette {_shown(path)} has no 'output' object")
        usage = raw.get("usage")
        if isinstance(usage, dict) and not all(_is_count(usage.get(n)) for n in TOKEN_FIELDS):
            raise LlmError(f"cassette {_shown(path)} has token counts that are not whole numbers")
        stop_reason, digest = raw.get("stop_reason"), raw.get("request_hash")
        if not all(isinstance(v, str | None) for v in (stop_reason, digest)):
            raise LlmError(f"cassette {_shown(path)}: stop_reason and request_hash must be strings")
        return CassetteEntry(
            output=raw["output"],
            model=str(raw.get("model") or ""),
            request_hash=digest,
            usage=usage if isinstance(usage, dict) else None,
            stop_reason=stop_reason,
        )

    def save(self, key: CassetteKey, entry: CassetteEntry) -> Path:
        path = self.path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        doc = {
            "version": FORMAT_VERSION,
            "key": asdict(key),
            "model": entry.model,
            "request_hash": entry.request_hash,
            "output": entry.output,
            "usage": entry.usage,
            "stop_reason": entry.stop_reason,
        }
        tmp = path.with_suffix(".json.tmp")
        try:
            tmp.write_text(json.dumps(doc, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
            os.replace(tmp, path)
        except BaseException:  # a half-written file must not linger next to the cassettes
            with contextlib.suppress(OSError):
                tmp.unlink(missing_ok=True)
            raise
        return path
