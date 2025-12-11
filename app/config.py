from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional

import yaml


BASE_DIR = Path(__file__).resolve().parent.parent
CONFIG_PATH = BASE_DIR / "config" / "config.yaml"


@dataclass
class ApiConfig:
    name: str
    url: str
    method: str
    interval_seconds: int
    max_retries: int
    retry_backoff_seconds: int
    timeout_seconds: int
    target_url: Optional[str]
    enabled: bool = True
    query_params: Optional[Dict[str, str]] = None  # ← پارامترهای داینامیک


@dataclass
class AppConfig:
    apis: List[ApiConfig]


def load_config(path: Path = CONFIG_PATH) -> AppConfig:
    if not path.exists():
        raise FileNotFoundError(f"Config file not found: {path}")

    with path.open("r", encoding="utf-8") as f:
        raw = yaml.safe_load(f) or {}

    apis_data = raw.get("apis")
    if apis_data is None or not isinstance(apis_data, list):
        raise ValueError("Config file must contain a top-level 'apis' list.")

    apis: List[ApiConfig] = []

    for entry in apis_data:
        if not isinstance(entry, dict):
            raise ValueError("Each item in 'apis' must be an object/dict.")

        name = entry.get("name")
        if not name:
            raise ValueError("Each API config must have a 'name' field.")

        url = entry.get("url")
        if not url:
            raise ValueError(f"API '{name}' must have a 'url' field.")

        raw_query_params = entry.get("query_params")
        query_params: Optional[Dict[str, str]] = None
        if raw_query_params is not None:
            if not isinstance(raw_query_params, dict):
                raise ValueError(
                    f"API '{name}' has invalid 'query_params' (must be an object/dict).",
                )

            query_params = {str(k): str(v) for k, v in raw_query_params.items()}

        api = ApiConfig(
            name=str(name),
            url=str(url),
            method=str(entry.get("method", "GET")).upper(),
            interval_seconds=int(entry.get("interval_seconds", 60)),
            max_retries=int(entry.get("max_retries", 3)),
            retry_backoff_seconds=int(entry.get("retry_backoff_seconds", 5)),
            timeout_seconds=int(entry.get("timeout_seconds", 10)),
            target_url=str(entry["target_url"]) if entry.get("target_url") else None,
            enabled=bool(entry.get("enabled", True)),
            query_params=query_params,
        )

        apis.append(api)

    if not apis:
        raise ValueError("No APIs configured in config.yaml.")

    return AppConfig(apis=apis)
