"""Config + secrets loading. YAML for human-edited config, .env for secrets."""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml

try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:  # dotenv optional; env vars may be set another way
    pass

ROOT = Path(__file__).resolve().parent.parent


def load_yaml(path: str | Path) -> Any:
    p = Path(path)
    if not p.is_absolute():
        p = ROOT / p
    if not p.exists():
        raise FileNotFoundError(f"config not found: {p} (copy the .example.yaml and edit it)")
    with open(p) as f:
        return yaml.safe_load(f) or {}


def load_sources(path: str | Path = "config/sources.yaml") -> list[dict]:
    """Returns [{type, company}, ...]. Falls back to the example file if the
    real one hasn't been created yet, so a fresh clone still runs."""
    try:
        data = load_yaml(path)
    except FileNotFoundError:
        data = load_yaml("config/sources.example.yaml")
    return data.get("sources", [])


def env(key: str, default: str | None = None) -> str | None:
    return os.environ.get(key, default)
