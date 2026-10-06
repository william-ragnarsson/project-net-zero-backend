"""Filesystem locations, resolved from the package, never from the cwd."""

from __future__ import annotations

from pathlib import Path

PACKAGE_DIR = Path(__file__).resolve().parent
ROOT = PACKAGE_DIR.parent

WEB_DIR = ROOT / "web"
WEB_DIST = WEB_DIR / "dist"
WEB_PUBLIC_REPLAYS = WEB_DIR / "public" / "replays"
SCHEMA_OUT = WEB_DIR / "src" / "gen" / "schema.json"

HARNESS_DIR = PACKAGE_DIR / "sandbox" / "harness"
DEMO_REPO = ROOT / "examples" / "demo-repo"
CASSETTES_DIR = DEMO_REPO / ".netzero-cassettes"
CACHE_DIR = ROOT / ".netzero-cache"
DEFAULT_RUNS_DIR = ROOT / "runs"
POWER_PROFILE = CACHE_DIR / "power-profile.json"
