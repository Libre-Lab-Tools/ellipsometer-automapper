"""
ABSTRACT
--------
Portable machine configuration for Ellipsometer AutoMapper.

Only computer-specific folder locations live in config.json. Paths may be
absolute or relative. Relative paths are resolved from the AutoMapper project
folder so a development copy can run on any computer without hard-coded paths.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent
CONFIG_PATH = PROJECT_ROOT / "config.json"


@dataclass(frozen=True)
class AppConfig:
    staging_folder: Path
    default_experiment_parent: Path


def _resolve_path(value: str) -> Path:
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = PROJECT_ROOT / path
    return path.resolve()


def load_config(path: str | Path = CONFIG_PATH) -> AppConfig:
    """Load config.json and create the configured data directories if needed."""
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(
            f"AutoMapper configuration file was not found: {path}\n"
            "Create config.json from config.example.json."
        )

    with path.open("r", encoding="utf-8") as file:
        raw = json.load(file)

    staging = _resolve_path(raw["staging_folder"])
    parent = _resolve_path(raw["default_experiment_parent"])

    staging.mkdir(parents=True, exist_ok=True)
    parent.mkdir(parents=True, exist_ok=True)

    return AppConfig(
        staging_folder=staging,
        default_experiment_parent=parent,
    )
