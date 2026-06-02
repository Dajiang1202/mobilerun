"""Configuration loader with layered config support.

Three-layer config:
  1. Global (~/.gameauto/settings.yaml)      — device, VLM keys (shared)
  2. Skill default (skills/<game>/config.yaml) — game defaults (bundled with code)
  3. User game (~/.gameauto/games/<game>.yaml) — per-game user overrides

Resolution: skill default → user game override → CLI/env override
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import yaml

APP_NAME = "gameauto"
GLOBAL_CONFIG_FILE = "settings.yaml"


def _user_config_dir() -> Path:
    return Path.home() / f".{APP_NAME}"


def _global_config_path() -> Path:
    return _user_config_dir() / GLOBAL_CONFIG_FILE


def _user_game_config_path(game: str) -> Path:
    return _user_config_dir() / "games" / f"{game}.yaml"


def _skill_config_path(game: str) -> Path:
    """Return path to skill's bundled default config."""
    skill_dir = Path(__file__).parent.parent / "skills" / game
    if not skill_dir.exists():
        raise FileNotFoundError(f"Skill not found: {game}")
    return skill_dir / "config.yaml"


# ── Public API ──────────────────────────────────────────────────────────


def load_global_config(config_path: str | Path | None = None) -> dict[str, Any]:
    """Load global config (device, VLM credentials).

    Resolution order:
      1. Explicit --config path
      2. GAMEAUTO_CONFIG env var
      3. ~/.gameauto/settings.yaml
      4. Init from project defaults
    """
    framework_defaults = _load_yaml(Path(__file__).parent / "default.yaml")

    user_dict: dict[str, Any] = {}
    if config_path:
        user_dict = _load_file(Path(config_path))
    elif env_path := os.environ.get("GAMEAUTO_CONFIG"):
        if Path(env_path).exists():
            user_dict = _load_file(Path(env_path))
    elif _global_config_path().exists():
        user_dict = _load_file(_global_config_path())
    else:
        _init_global_config(framework_defaults)
        print(f"[GameAuto] Created global config at {_global_config_path()}")
        print(f"           Edit this file to set your VLM API key and device serial.")

    return _resolve_env(_deep_merge(framework_defaults, user_dict))


def load_game_config(game: str) -> dict[str, Any]:
    """Load game-specific config.

    Resolution: skill default → ~/.gameauto/games/<game>.yaml → env override
    """
    # 1. Skill default
    skill_path = _skill_config_path(game)
    game_defaults = _load_yaml(skill_path) if skill_path.exists() else {}

    # 2. User game override
    user_game_path = _user_game_config_path(game)
    user_overrides = _load_file(user_game_path) if user_game_path.exists() else {}

    return _resolve_env(_deep_merge(game_defaults, user_overrides))


def save_global_config(config: dict[str, Any]) -> Path:
    p = _global_config_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "w", encoding="utf-8") as f:
        yaml.dump(config, f, default_flow_style=False, sort_keys=False, allow_unicode=True)
    return p


# ── internals ──────────────────────────────────────────────────────────


def _init_global_config(defaults: dict) -> None:
    user_config = {
        "_comment": "GameAuto global config — device and VLM credentials shared by all games.",
        "device": defaults.get("device", {}),
        "vlm": defaults.get("vlm", {}),
    }
    save_global_config(user_config)


def _load_yaml(path: Path) -> dict:
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def _load_file(path: Path) -> dict:
    text = path.read_text(encoding="utf-8")
    if path.suffix in (".json",):
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            pass
    return yaml.safe_load(text) or {}


def _deep_merge(base: dict, override: dict) -> dict:
    result = dict(base)
    for key, val in override.items():
        if key.startswith("_"):
            continue
        if isinstance(val, dict) and isinstance(result.get(key), dict):
            result[key] = _deep_merge(result[key], val)
        elif val is not None and val != "":
            result[key] = val
    return result


_ENV_VAR_RE = __import__("re").compile(r"\$\{(\w+)\}")


def _resolve_env(obj: Any) -> Any:
    if isinstance(obj, str):
        def _replace(m):
            return os.environ.get(m.group(1), "")
        return _ENV_VAR_RE.sub(_replace, obj)
    if isinstance(obj, dict):
        return {k: _resolve_env(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_resolve_env(v) for v in obj]
    return obj
