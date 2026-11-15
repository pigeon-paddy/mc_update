"""JSON file cache for API results."""

from __future__ import annotations

import json
import os
import sys
import tempfile
import time
from dataclasses import asdict
from pathlib import Path

from ..config.default_config import CACHE_PATH, CACHE_TTL, MISS_TTL
from .classes import Project


# --------------------------------------------------------------------------- #
# Cache
# --------------------------------------------------------------------------- #
class ProjectCache:
    """
    Single JSON file: { "<source>:<name>": {"fetched_at": epoch, "project": {...} | null} }

    - fresh entry          -> returned without any API call
    - missing/expired      -> fetch from API, store result
    - API fails + stale    -> stale entry is returned with a warning
    - "not found" results are cached too (project=null) with a shorter TTL
    """

    def __init__(self, path: Path = CACHE_PATH, ttl: int = CACHE_TTL, miss_ttl: int = MISS_TTL):
        self.path, self.ttl, self.miss_ttl = Path(path), ttl, miss_ttl
        self._data: dict[str, dict] = self._load()

    # -- persistence --
    def _load(self) -> dict:
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError):
            return {}                                   # missing or corrupt -> start fresh

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=self.path.parent, suffix=".tmp")
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(self._data, f, indent=2, ensure_ascii=False)
        os.replace(tmp, self.path)                      # atomic: no half-written cache

    # -- logic --
    @staticmethod
    def key(source: str, name: str, project_type: str = "mod") -> str:
        prefix = source if project_type == "mod" else f"{source}:{project_type}"
        return f"{prefix}:{name.strip().lower()}"

    def _is_fresh(self, entry: dict) -> bool:
        ttl = self.ttl if entry["project"] else self.miss_ttl
        return time.time() - entry["fetched_at"] < ttl

    @staticmethod
    def _to_project(entry: dict) -> Project | None:
        return Project.from_dict(entry["project"]) if entry["project"] else None

    def fetch(self, source: str, name: str, fetch_fn, refresh: bool = False,
              project_type: str = "mod") -> Project | None:
        key = self.key(source, name, project_type)
        entry = self._data.get(key)

        if entry and not refresh and self._is_fresh(entry):
            return self._to_project(entry)              # cache hit

        try:
            project = fetch_fn(name)                    # cache miss -> API
        except Exception as e:
            if entry:                                   # offline / rate limited: use stale data
                print(f"[cache] {key}: API failed ({e}); using stale entry", file=sys.stderr)
                return self._to_project(entry)
            raise

        self._data[key] = {
            "fetched_at": time.time(),
            "project": asdict(project) if project else None,
        }
        self._save()
        return project

    def fetch_value(self, key: str, fetch_fn, ttl: int, refresh: bool = False):
        """Same idea as fetch(), but for arbitrary JSON-serialisable values (e.g. the MC version list)."""
        entry = self._data.get(key)
        if entry and not refresh and time.time() - entry["fetched_at"] < ttl:
            return entry["value"]
        try:
            value = fetch_fn()
        except Exception as e:
            if entry:
                print(f"[cache] {key}: API failed ({e}); using stale entry", file=sys.stderr)
                return entry["value"]
            raise
        self._data[key] = {"fetched_at": time.time(), "value": value}
        self._save()
        return value

    def clear(self) -> None:
        self._data = {}
        self._save()
