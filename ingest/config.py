"""Loads config/leagues/*.yml."""
from __future__ import annotations

from pathlib import Path

import yaml

CONFIG_DIR = Path("config/leagues")


def load_league_config(slug: str) -> dict:
    path = CONFIG_DIR / f"{slug}.yml"
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def load_all_league_configs() -> list[dict]:
    return [load_league_config(p.stem) for p in sorted(CONFIG_DIR.glob("*.yml"))]
