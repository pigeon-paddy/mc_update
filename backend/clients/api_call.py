#!/usr/bin/env python3
"""
Look up a Minecraft mod on Modrinth and CurseForge and return a Project object.

Setup:
    copy .env.example to .env and fill it in (USER_AGENT, MOD_LIST_PATH, CURSEFORGE_API_KEY).
    The CurseForge key is available at https://console.curseforge.com

Usage:
    python mod_lookup.py                 # read the mod list from MOD_LIST_PATH
    python mod_lookup.py Dreamshift      # test a single mod
"""
from __future__ import annotations

import os
import re
import sys
import json
import time
import tempfile
from collections.abc import Callable
from pathlib import Path
from dataclasses import dataclass, field, asdict, fields

import requests

# --------------------------------------------------------------------------- #
# Config (private values live in .env, see .env.example)
# --------------------------------------------------------------------------- #
def load_env(*paths: Path) -> None:
    """
    Minimal .env reader (KEY=VALUE per line, '#' comments, optional 'export ' prefix and quotes).
    Values are taken literally, so Windows paths with backslashes work unquoted.
    Variables already set (and non-empty) in the real environment are not overridden.
    """
    for path in paths:
        try:
            text = path.read_text(encoding="utf-8-sig")
        except OSError:
            continue
        for line in text.splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            if line.startswith("export "):
                line = line[7:].lstrip()
            key, value = line.split("=", 1)
            key, value = key.strip(), value.strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
                value = value[1:-1]
            if key and not os.environ.get(key):     # fills unset *and* empty variables
                os.environ[key] = value


load_env(
    Path(__file__).resolve().parents[2] / ".env",   # two levels above the script's folder
    Path.cwd() / ".env",
)


def require_env(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        sys.exit(f"Missing {name}: set it in .env (see .env.example) or as an environment variable.")
    return value


def user_agent() -> str:
    """Modrinth requires a identifying User-Agent, e.g. 'name/mod-update-script/0.1 (mail@example.com)'."""
    return require_env("USER_AGENT")


TIMEOUT = 15

MODRINTH_API = "https://api.modrinth.com/v2"
CURSEFORGE_API = "https://api.curseforge.com/v1"
CF_GAME_ID_MINECRAFT = 432
# Project types, searched in this order. Each later type is only tried if the earlier ones found nothing.
SEARCH_TYPES = ("mod", "datapack", "plugin", "resourcepack")
# CurseForge class ids (confirm with GET /v1/categories?gameId=432&classesOnly=true)
CF_CLASS_IDS = {"mod": 6, "datapack": 6945, "plugin": 5, "resourcepack": 12}     # 5 = Bukkit Plugins

# CurseForge ModLoaderType enum -> name
CF_LOADERS = {
    0: "any", 1: "forge", 2: "cauldron", 3: "liteloader",
    4: "fabric", 5: "quilt", 6: "neoforge",
}

# Cache: one JSON file, entries expire after CACHE_TTL seconds
CACHE_PATH = Path(os.environ.get("MOD_CACHE", "~/.cache/mod-updater/projects.json")).expanduser()
CACHE_TTL = 24 * 3600      # successful lookups: 24 h
MISS_TTL = 1 * 3600        # "not found" results: 1 h

# Modrinth lists loaders (fabric, forge, ...) inside "categories"; they are not real categories
LOADER_TAGS = {"fabric", "forge", "neoforge", "quilt", "liteloader", "rift", "modloader",
               "bukkit", "spigot", "paper", "purpur", "folia", "sponge", "bungeecord",
               "velocity", "waterfall", "datapack", "minecraft"}

# Export layout: heading -> ordered sub-categories. Each sub-category is (display name, alias, alias, ...).
# Aliases cover Modrinth tags and CurseForge category names (matched ignoring case/punctuation).
# Mods are sorted by sub-category in the order listed here, then by title; no sub-headings are written.
# Edit freely - anything that matches nothing ends up under "Other".
CATEGORY_GROUPS: dict[str, list[tuple[str, ...]]] = {
    "Performance & Server": [
        ("Optimization", "optimization", "performance"),
        ("Server", "server utility", "server", "management"),
        ("Library", "library", "library / api", "api and library"),
    ],
    "Technology & Automation": [
        ("Technology", "technology"),
        ("Redstone", "redstone"),
        ("Storage", "storage"),
        ("Transportation", "transportation"),
    ],
    "World & Exploration": [
        ("World generation", "worldgen", "world gen", "world generation"),
        ("Adventure", "adventure", "adventure and rpg"),
        ("Mobs", "mobs"),
        ("Map & information", "map and information", "information"),
    ],
    "Gameplay": [
        ("Game mechanics", "game-mechanics", "game mechanics"),
        ("Magic", "magic"),
        ("Equipment", "equipment", "armor, tools, and weapons"),
        ("Food", "food", "food and cooking"),
        ("Economy", "economy"),
        ("Minigame", "minigame"),
        ("Cursed", "cursed"),
    ],
    "Visuals & Quality of Life": [
        ("Decoration", "decoration"),
        ("Cosmetic", "cosmetic"),
        ("Utility", "utility", "utility & qol", "qol"),
        ("Social", "social"),
        ("Education", "education"),
    ],
    "Other": [
        ("Miscellaneous", "miscellaneous"),
    ],
}
FALLBACK_HEADING = "Other"

MC_VERSIONS_TTL = 6 * 3600   # list of MC releases: 6 h
DEFAULT_MIN_VERSION = "1.21.1"

RELEASE_RE = re.compile(r"^\d+\.\d+(\.\d+)?$")  # skips snapshots / pre-releases


# --------------------------------------------------------------------------- #
# Data classes
# --------------------------------------------------------------------------- #
def version_key(v: str) -> tuple[int, ...]:
    """
    Sortable key for release versions. Works across the 2026 scheme change:
        1.21.1 -> (1, 21, 1)  <  1.21.11 -> (1, 21, 11)  <  26.1 -> (26, 1)  <  26.1.1 -> (26, 1, 1)
    (old scheme is 1.<major>.<patch>, new scheme is <year>.<drop>.<hotfix>)
    """
    return tuple(int(p) for p in v.split("."))


@dataclass
class Project:
    source: str                      # "modrinth" | "curseforge"
    id: str | int
    title: str
    url: str
    slug: str = ""
    postfix: str = ""                # e.g. latest file / version name, used as suffix
    description: str = ""
    category: list[str] = field(default_factory=list)
    versions: list[str] = field(default_factory=list)    # MC versions, newest first
    modloader: list[str] = field(default_factory=list)
    downloads: int = 0
    updated: str = ""
    project_type: str = "mod"                            # mod | datapack | plugin | resourcepack
    version_loaders: dict[str, list[str]] = field(default_factory=dict)   # MC version -> loaders supported on it

    def __post_init__(self):
        if not self.version_loaders and self.versions:    # cache entries from before this field existed
            self.version_loaders = {v: list(self.modloader) for v in self.versions}

    @classmethod
    def from_dict(cls, d: dict) -> "Project":
        known = {f.name for f in fields(cls)}          # ignore keys from older/newer schemas
        return cls(**{k: v for k, v in d.items() if k in known})

    def supports(self, mc_version: str) -> bool:
        return mc_version in self.versions

    def pretty(self) -> str:
        w = 12
        rows = [
            ("Source", self.source),
            ("Type", self.project_type),
            ("ID", self.id),
            ("Title", self.title),
            ("URL", self.url),
            ("Slug", self.slug),
            ("Postfix", self.postfix),
            ("Category", ", ".join(self.category)),
            ("Modloader", ", ".join(self.modloader)),
            ("Downloads", f"{self.downloads:,}"),
            ("Updated", self.updated),
            ("MC versions", ", ".join(self.versions)),
            ("Description", self.description),
        ]
        line = "─" * 60
        out = [line, f" {self.title}  [{self.source}]", line]
        out += [f" {k:<{w}} {v}" for k, v in rows]
        out.append(line)
        return "\n".join(out)

    def __str__(self) -> str:
        return self.pretty()

    def to_json(self) -> str:
        return json.dumps(asdict(self), indent=2)


# --------------------------------------------------------------------------- #
# Modrinth
# --------------------------------------------------------------------------- #
def modrinth_get(project_name: str, project_type: str = "mod") -> Project | None:
    s = requests.Session()
    s.headers["User-Agent"] = user_agent()

    # 1) search -> best hit
    r = s.get(
        f"{MODRINTH_API}/search",
        params={
            "query": project_name,
            "facets": json.dumps([[f"project_type:{project_type}"]]),
            "limit": 5,
        },
        timeout=TIMEOUT,
    )
    r.raise_for_status()
    hits = r.json()["hits"]
    if not hits:
        return None
    # prefer exact title match, else first hit (already ranked by relevance)
    hit = next((h for h in hits if h["title"].lower() == project_name.lower()), hits[0])

    # 2) full project details
    r = s.get(f"{MODRINTH_API}/project/{hit['project_id']}", timeout=TIMEOUT)
    r.raise_for_status()
    p = r.json()

    # 3) latest version -> postfix (version_number, e.g. "1.2.3+mc1.21")
    r = s.get(f"{MODRINTH_API}/project/{p['id']}/version", timeout=TIMEOUT)
    r.raise_for_status()
    vers = r.json()
    postfix = vers[0]["version_number"] if vers else ""

    # (MC version -> loaders) pairs from the individual files, so a mod that is Fabric-only on 1.21.1
    # but NeoForge-only on 26.1 is reported correctly per version
    vl: dict[str, set[str]] = {}
    for ver in vers:
        for gv in ver.get("game_versions", []):
            if RELEASE_RE.match(gv):
                vl.setdefault(gv, set()).update(l.lower() for l in ver.get("loaders", []))
    if not vl:                                            # no files listed: fall back to project-level lists
        for gv in p.get("game_versions", []):
            if RELEASE_RE.match(gv):
                vl[gv] = {l.lower() for l in p.get("loaders", [])}
    mc_versions = sorted(vl, key=version_key, reverse=True)

    return Project(
        source="modrinth",
        id=p["id"],
        title=p["title"],
        url=f"https://modrinth.com/{p.get('project_type', project_type)}/{p['slug']}",
        slug=p["slug"],
        postfix=postfix,
        description=p["description"],
        category=[c for c in p.get("categories", []) + p.get("additional_categories", [])
                  if c.lower() not in LOADER_TAGS],
        versions=mc_versions,
        version_loaders={v: sorted(ls) for v, ls in vl.items()},
        modloader=sorted(p.get("loaders", [])),
        downloads=p.get("downloads", 0),
        updated=p.get("updated", ""),
        project_type=project_type,
    )


# --------------------------------------------------------------------------- #
# CurseForge
# --------------------------------------------------------------------------- #
def curseforge_get(project_name: str, api_key: str | None = None, project_type: str = "mod") -> Project | None:
    api_key = api_key or require_env("CURSEFORGE_API_KEY")

    s = requests.Session()
    s.headers.update({"x-api-key": api_key, "Accept": "application/json",
                      "User-Agent": user_agent()})

    # 1) search
    r = s.get(
        f"{CURSEFORGE_API}/mods/search",
        params={
            "gameId": CF_GAME_ID_MINECRAFT,
            "classId": CF_CLASS_IDS[project_type],
            "searchFilter": project_name,
            "sortField": 2,      # popularity
            "sortOrder": "desc",
            "pageSize": 5,
        },
        timeout=TIMEOUT,
    )
    r.raise_for_status()
    hits = r.json()["data"]
    if not hits:
        return None
    hit = next((h for h in hits if h["name"].lower() == project_name.lower()), hits[0])

    # 2) full mod details
    r = s.get(f"{CURSEFORGE_API}/mods/{hit['id']}", timeout=TIMEOUT)
    r.raise_for_status()
    m = r.json()["data"]

    # latestFilesIndexes: one entry per (gameVersion, modLoader) with the newest file
    idx = m.get("latestFilesIndexes", [])
    vl: dict[str, set[str]] = {}
    for i in idx:
        gv = i["gameVersion"]
        if not RELEASE_RE.match(gv):
            continue
        ml = i.get("modLoader")
        if project_type == "plugin":
            name = "bukkit"                               # CurseForge class 5 = Bukkit plugins
        elif ml is None:
            name = "any"                                  # no loader info (e.g. libraries, packs)
        else:
            name = CF_LOADERS.get(ml, "unknown")
        vl.setdefault(gv, set()).add(name)
    mc_versions = sorted(vl, key=version_key, reverse=True)
    loaders = sorted({l for ls in vl.values() for l in ls})
    latest = m.get("latestFiles", [])
    postfix = latest[0]["displayName"] if latest else ""

    return Project(
        source="curseforge",
        id=m["id"],
        title=m["name"],
        url=m["links"]["websiteUrl"],
        slug=m["slug"],
        postfix=postfix,
        description=m["summary"],
        category=[c["name"] for c in m.get("categories", [])],
        versions=mc_versions,
        version_loaders={v: sorted(ls) for v, ls in vl.items()},
        modloader=loaders,
        downloads=m.get("downloadCount", 0),
        updated=m.get("dateModified", ""),
        project_type=project_type,
    )


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


# --------------------------------------------------------------------------- #
# Your MC config + compatibility / best-fit logic (from the sketch)
# --------------------------------------------------------------------------- #
def _fetch_release_versions() -> list[str]:
    """All Minecraft *release* versions known to Modrinth (excludes snapshots, pre-releases, RCs, betas)."""
    r = requests.get(f"{MODRINTH_API}/tag/game_version",
                     headers={"User-Agent": user_agent()}, timeout=TIMEOUT)
    r.raise_for_status()
    return [v["version"] for v in r.json()
            if v.get("version_type") == "release" and RELEASE_RE.match(v["version"])]


def mc_versions_between(min_version: str = DEFAULT_MIN_VERSION,
                        max_version: str | None = None,
                        cache: ProjectCache | None = None,
                        refresh: bool = False) -> list[str]:
    """
    Release versions from min_version up to max_version (None = newest available),
    returned newest -> oldest. Handles 1.21.11 -> 26.1 and later (26.1.1, 26.2, 26.3, ...).
    """
    cache = cache or ProjectCache()
    all_versions = cache.fetch_value("_meta:mc_release_versions", _fetch_release_versions,
                                     MC_VERSIONS_TTL, refresh=refresh)
    lo = version_key(min_version)
    hi = version_key(max_version) if max_version else None
    return sorted(
        (v for v in all_versions
         if version_key(v) >= lo and (hi is None or version_key(v) <= hi)),
        key=version_key, reverse=True,
    )


@dataclass
class MC:
    loader: str | None          # None = any loader (compare them in the exported table)
    versions: list[str]          # newest -> oldest
    min_version: str
    max_version: str

    @classmethod
    def from_api(cls, loader: str | None, min_version: str = DEFAULT_MIN_VERSION,
                 max_version: str | None = None, cache: ProjectCache | None = None,
                 refresh: bool = False) -> "MC":
        versions = mc_versions_between(min_version, max_version, cache, refresh)
        if not versions:
            raise ValueError(f"no Minecraft releases found between {min_version} and {max_version or 'newest'}")
        return cls(loader, versions, min_version, versions[0])   # max_version resolved to newest

    def target_versions(self) -> list[str]:
        """Configured versions, newest -> oldest, clipped to [min, max]."""
        lo, hi = version_key(self.min_version), version_key(self.max_version)
        return [v for v in sorted(self.versions, key=version_key, reverse=True)
                if lo <= version_key(v) <= hi]


def supports_on(p: Project, version: str, loader: str | None = None) -> bool:
    """MC version supported (and, for mods, the given loader if one is requested)."""
    if version not in p.version_loaders:
        return False
    if loader is None or p.project_type != "mod":          # datapacks/plugins/resource packs: version only
        return True
    loaders = {l.lower() for l in p.version_loaders[version]}
    return loader.lower() in loaders or "any" in loaders


def build_compatible(mc: MC, mods: dict[str, list[Project]]) -> dict[str, list[Project]]:
    """
    mods: {name from the list: [Project found]}  (at most one project per name)
    Returns {mc version: [compatible Projects]}. A mod counts for a version if any loader supports it,
    unless a loader was requested (--loader=...).
    """
    compatible: dict[str, list[Project]] = {}
    for version in mc.target_versions():          # newest -> oldest, already clipped to [min, max]
        compatible[version] = []
        seen: set[tuple] = set()                  # two list entries can resolve to the same project
        for projs in mods.values():
            match = next((p for p in projs if supports_on(p, version, mc.loader)), None)
            if match and (match.source, match.id) not in seen:
                seen.add((match.source, match.id))
                compatible[version].append(match)
    return compatible


@dataclass
class BestFit:
    version: str | None = None
    size: int = 0


def best_fit(compatible: dict[str, list[Project]]) -> BestFit:
    best = BestFit()
    for version, projs in compatible.items():
        if len(projs) > best.size:                # strict > keeps the newest on ties
            best = BestFit(version, len(projs))
    return best


# --------------------------------------------------------------------------- #
# Combined lookup + demo
# --------------------------------------------------------------------------- #
def lookup(name: str, cache: ProjectCache | None = None, refresh: bool = False) -> list[Project]:
    """
    Per type (mod -> datapack -> plugin -> resource pack): Modrinth first, CurseForge only if Modrinth
    has nothing (and only if an API key is set). The first hit wins; later types are not searched.
    Returns a list with at most one project.
    """
    cache = cache or ProjectCache()
    sources: list[tuple[str, Callable[..., Project | None]]] = [("modrinth", modrinth_get)]
    if os.environ.get("CURSEFORGE_API_KEY", "").strip():
        sources.append(("curseforge", curseforge_get))
    elif not getattr(lookup, "_warned", False):          # warn once, not once per mod
        print("[curseforge] CURSEFORGE_API_KEY not set - searching Modrinth only", file=sys.stderr)
        lookup._warned = True                            # type: ignore[attr-defined]

    for ptype in SEARCH_TYPES:
        for source, fn in sources:                       # Modrinth first, then CurseForge
            try:
                p = cache.fetch(source, name, lambda n, fn=fn, t=ptype: fn(n, project_type=t),
                                refresh=refresh, project_type=ptype)
            except Exception as e:                       # keep going if one platform fails
                print(f"[{source}] {ptype} search for {name!r} failed: {e}", file=sys.stderr)
                continue
            if p:
                if ptype != "mod":
                    print(f"[lookup] {name!r} is not a mod - found as {ptype} on {source}", file=sys.stderr)
                return [p]

    print(f"[lookup] nothing found for {name!r} (searched: {', '.join(SEARCH_TYPES)})", file=sys.stderr)
    return []


# --------------------------------------------------------------------------- #
# Ranking + markdown export
# --------------------------------------------------------------------------- #
def top_versions(compatible: dict[str, list[Project]], n: int = 3) -> list[str]:
    """
    Best n MC versions that have at least one compatible mod, ordered by
      1. most supported mods
      2. newest MC version (tie-break)
    """
    versions = sorted((v for v, ps in compatible.items() if ps), key=version_key, reverse=True)
    versions.sort(key=lambda v: len(compatible[v]), reverse=True)    # stable: newest stays first on ties
    return versions[:n]


def _norm(text: str) -> str:
    return re.sub(r"[^a-z0-9]", "", text.lower())


# normalised alias -> (heading, position of the sub-category in the heading)
_CATEGORY_INDEX: dict[str, tuple[str, int]] = {
    _norm(alias): (heading, pos)
    for heading, subs in CATEGORY_GROUPS.items()
    for pos, sub in enumerate(subs)
    for alias in sub
}


RESOLUTION_RE = re.compile(r"^\d+x[+-]?$")          # resource pack resolutions (16x, 512x+, 8x-) are no categories
# Display order of the types (same order as the search fallback: mod -> datapack -> plugin -> resource pack)
TYPE_ORDER = (("mod", "Mods"), ("datapack", "Datapacks"),
              ("plugin", "Plugins"), ("resourcepack", "Resource Packs"))
# Datapacks and resource packs work with every loader -> they get a mark in every loader column
UNIVERSAL_TYPES = ("datapack", "resourcepack")
NON_COLUMN_LOADERS = {"datapack", "minecraft", "any", "unknown"}     # tags that are not a loader column
LOADER_ORDER = ("fabric", "neoforge", "forge", "quilt", "paper", "purpur", "spigot", "bukkit", "folia", "sponge")
LOADER_NAMES = {"neoforge": "NeoForge"}
SHOW_LOADER_SUMMARY = True                            # one-line "supported x/y per loader" table at the top
# What itzg/minecraft-server can install per platform (resource packs are not installable that way)
DEPLOY_TYPES = {"modrinth": ("mod", "plugin", "datapack"), "curseforge": ("mod", "plugin")}


def _loaders_on(p: Project, version: str) -> set[str]:
    return {l.lower() for l in p.version_loaders.get(version, [])}


def loader_columns(projects: list[Project], version: str) -> list[str]:
    """Loader columns for this file: every real loader any mod/plugin supports on this MC version."""
    cols: set[str] = set()
    for p in projects:
        if p.project_type not in UNIVERSAL_TYPES:
            cols |= _loaders_on(p, version) - NON_COLUMN_LOADERS
    known = [l for l in LOADER_ORDER if l in cols]
    return known + sorted(cols - set(known))


def loader_cells(p: Project, version: str, columns: list[str]) -> set[str]:
    """Which loader columns get a mark for this project (a mod can fill several)."""
    if p.project_type in UNIVERSAL_TYPES:
        return set(columns)
    loaders = _loaders_on(p, version)
    return set(columns) if "any" in loaders else loaders & set(columns)


def categorize(project: Project) -> tuple[str, int]:
    """
    ('## section heading', sort position inside that section).
    Mods: first recognised category -> configured group. Datapacks / plugins / resource packs: their
    own first category as heading ("Vanilla Like", "Management", ...), "Other" if they have none.
    """
    cats = [c for c in project.category
            if c.lower() not in LOADER_TAGS and not RESOLUTION_RE.match(c)]
    if project.project_type != "mod":
        return (re.sub(r"[-_]+", " ", cats[0]).title() if cats else FALLBACK_HEADING), 0
    for cat in cats:
        hit = _CATEGORY_INDEX.get(_norm(cat))
        if hit:
            return hit
    return FALLBACK_HEADING, 999            # no/unknown category: end of the "Other" section


def _sections(projects: list[Project]) -> list[tuple[str, list[tuple[str, list[Project]]]]]:
    """[(type heading, [(section heading, projects in display order)])] - one ordering for both views."""
    out = []
    for ptype, type_heading in TYPE_ORDER:
        of_type = [p for p in projects if p.project_type == ptype]
        if not of_type:
            continue
        sections: dict[str, list[tuple[int, str, Project]]] = {}
        for p in of_type:
            heading, pos = categorize(p)
            sections.setdefault(heading, []).append((pos, p.title.lower(), p))
        if ptype == "mod":
            order = list(CATEGORY_GROUPS)                                              # configured order
        else:
            order = sorted(sections, key=lambda h: (h == FALLBACK_HEADING, h.lower()))  # A-Z, Other last
        out.append((type_heading, [(h, [e[2] for e in sorted(sections[h], key=lambda e: e[:2])])
                                   for h in order if h in sections]))
    return out


def _loader_label(loader: str) -> str:
    return LOADER_NAMES.get(loader, loader.title())


def _row(cells: list[str]) -> str:
    return "| " + " | ".join(cells) + " |"


def _name_cell(p: Project) -> str:
    title = p.title.replace("[", "\\[").replace("]", "\\]").replace("|", "\\|")
    return f"[{title}]({p.url})"


def _table(entries: list[Project], version: str, columns: list[str]) -> str:
    lines = [_row(["Name"] + [_loader_label(c) for c in columns]),
             _row(["---"] + [":-:"] * len(columns))]
    for p in entries:
        cells = loader_cells(p, version, columns)
        lines.append(_row([_name_cell(p)] + ["✓" if c in cells else "" for c in columns]))
    return "\n".join(lines)


def render_view(projects: list[Project], version: str) -> str:
    """
    User view, for reading and comparing:
      # Mods / # Datapacks / # Plugins / # Resource Packs
      ## <category section>      -> a table: one row per project, one column per loader
    A project that supports several loaders is marked in several columns.
    """
    columns = loader_columns(projects, version)
    parts = []
    if SHOW_LOADER_SUMMARY and columns:
        n = len(projects)
        counts = [sum(1 for p in projects if c in loader_cells(p, version, columns)) for c in columns]
        parts.append("\n".join([_row([""] + [_loader_label(c) for c in columns]),
                                _row(["---"] + [":-:"] * len(columns)),
                                _row(["**Supported**"] + [f"{k}/{n}" for k in counts])]))
    for type_heading, sections in _sections(projects):
        parts.append(f"# {type_heading}")
        for heading, entries in sections:
            parts.append(f"## {heading}\n\n{_table(entries, version, columns)}")
    return "\n\n".join(parts) + "\n"


def _deploy_entry(p: Project) -> str:
    return f"datapack:{p.slug}" if p.project_type == "datapack" else p.slug


def render_deployment(projects: list[Project], version: str) -> str:
    """
    Slug lists for itzg/minecraft-server, grouped by loader, then by platform
    (MODRINTH_PROJECTS / CURSEFORGE_FILES). Same order as the user view, without category headings.
    """
    columns = loader_columns(projects, version)
    ordered = [p for _, sections in _sections(projects) for _, entries in sections for p in entries]
    blocks = []
    for loader in columns or [None]:                          # no loader columns -> plain vanilla list
        sub = []
        for source, heading in (("modrinth", "Modrinth"), ("curseforge", "Curseforge")):
            entries = [_deploy_entry(p) for p in ordered
                       if p.source == source and p.slug and p.project_type in DEPLOY_TYPES[source]
                       and (loader is None or loader in loader_cells(p, version, columns))]
            if entries:
                sub.append(f"## {heading}\n\n```\n" + "\n".join(entries) + "\n```")
        if sub:
            label = _loader_label(loader) if loader else "Vanilla"
            blocks.append(f"# {label}\n\n" + "\n\n".join(sub))
    return "\n\n".join(blocks) + ("\n" if blocks else "")


def render_markdown(projects: list[Project], version: str) -> str:
    """Full file: user view, a rule, then the deployment lists."""
    deployment = render_deployment(projects, version)
    return render_view(projects, version) + (f"\n---\n\n{deployment}" if deployment else "")


def export_markdown(compatible: dict[str, list[Project]], out_dir: Path,
                    prefix: str = "MC_Update", n: int = 3) -> list[Path]:
    """Write <prefix>_<version>.md for the top n versions; existing files are replaced."""
    out_dir.mkdir(parents=True, exist_ok=True)
    written = []
    for version in top_versions(compatible, n):
        path = out_dir / f"{prefix}_{version}.md"
        fd, tmp = tempfile.mkstemp(dir=out_dir, suffix=".tmp")
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
            f.write(render_markdown(compatible[version], version))
        os.replace(tmp, path)                              # atomic replace of an existing file
        written.append(path)
    return written


def explain_misses(projects: list[Project], mc: MC) -> None:
    """Say *why* a project is not compatible (wrong loader vs. version outside the range)."""
    lo, hi = version_key(mc.min_version), version_key(mc.max_version)
    for p in projects:
        in_range = [v for v in p.versions if lo <= version_key(v) <= hi]
        outside = [v for v in p.versions if v not in in_range]
        print(f"  {p.title} [{p.source}, {p.project_type}] loaders={p.modloader or '-'} versions={p.versions or '-'}")
        if not in_range:
            print(f"    x no supported version inside {mc.min_version} .. {mc.max_version}"
                  + (f" (only outside the range: {', '.join(outside)})" if outside else ""))
        elif mc.loader and not any(supports_on(p, v, mc.loader) for v in in_range):
            print(f"    x loader '{mc.loader}' not supported on any version in the range")


def read_mod_list(path: str | Path) -> list[str]:
    """
    One mod per line; the whole line is the name (spaces are kept).
    Skipped: empty lines and markdown headings ('# ...', '## ...', ...). Duplicates are dropped.
    """
    names, seen = [], set()
    for line in Path(path).read_text(encoding="utf-8-sig").splitlines():   # utf-8-sig strips a BOM
        name = line.strip()
        if not name or name.startswith("#"):
            continue
        if name.lower() in seen:
            continue
        seen.add(name.lower())
        names.append(name)
    return names


def _opt_or_none(name: str) -> str | None:
    for a in sys.argv[1:]:
        if a.startswith(f"--{name}="):
            return a.split("=", 1)[1]
    return None


def _opt(name: str, default: str) -> str:
    """Value of --name=VALUE, or default (always a str)."""
    return _opt_or_none(name) or default


def main() -> None:
    """
    Default:  read mods from MOD_LIST_PATH in .env (or --file=PATH) and compute the best-fitting MC version.
    Test:     python mod_lookup.py "Mod Name"   -> look up just that one mod

    Flags (both modes): --loader=fabric (optional filter, default: all loaders)  --version=1.21.1 (one fixed MC version, skips the
    best-fit ranking)  --min=1.21.1  --max=26.3  --refresh  --verbose
    Export: list mode writes <list name>_<version>.md for the top 3 versions (--no-export to skip);
            test mode only does so with --export. Target folder: OUTPUT_DIR in .env, default = mod list folder.
    """
    positional = [a for a in sys.argv[1:] if not a.startswith("--")]
    refresh = "--refresh" in sys.argv
    verbose = "--verbose" in sys.argv
    list_path: Path | None = None

    if positional:                                   # test mode: single mod from the command line
        names = [" ".join(positional)]
        verbose = True                               # always show the full project info here
        do_export = "--export" in sys.argv           # test mode only writes files on request
    else:
        path = Path(_opt_or_none("file") or require_env("MOD_LIST_PATH"))
        try:
            names = read_mod_list(path)
        except OSError as e:
            sys.exit(f"Cannot read mod list {path}: {e}")
        list_path = path
        do_export = "--no-export" not in sys.argv
        print(f"Read {len(names)} mod(s) from {path}")
        if not names:
            sys.exit("Mod list is empty.")

    # export target: OUTPUT_DIR from .env, else next to the mod list, else the current folder
    out_dir = Path(os.environ.get("OUTPUT_DIR", "").strip() or (list_path.parent if list_path else Path.cwd()))
    prefix = list_path.stem if list_path else "MC_Update"

    cache = ProjectCache()
    mods: dict[str, list[Project]] = {}
    for name in names:
        mods[name] = lookup(name, cache=cache, refresh=refresh)
        if verbose:
            for p in mods[name]:
                print(p)

    by_project: dict[tuple, list[str]] = {}
    for name, ps in mods.items():
        for p in ps:
            by_project.setdefault((p.source, p.id), []).append(name)
    for (source, _), names_ in by_project.items():
        if len(names_) > 1:
            title = mods[names_[0]][0].title
            print(f"\nNote: these list entries all resolved to the same project "
                  f"'{title}' ({source}) and are counted once: " + " | ".join(names_))

    not_found = [n for n, ps in mods.items() if not ps]
    if not_found:
        print("\nNot found as mod, datapack, plugin or resource pack: " + ", ".join(not_found))
    if len(not_found) == len(mods):
        sys.exit("No projects found (check stderr above / CURSEFORGE_API_KEY).")

    only = _opt_or_none("version")                   # --version=X: one fixed MC version, no best-fit ranking
    try:
        mc = MC.from_api(loader=_opt_or_none("loader"),
                         min_version=only or _opt("min", DEFAULT_MIN_VERSION),
                         max_version=only or _opt_or_none("max"), cache=cache, refresh=refresh)
    except ValueError as e:
        sys.exit(f"{e} (is {only!r} a released Minecraft version?)" if only else str(e))
    print(f"\nLoader {mc.loader or 'any'}, MC releases {mc.min_version} -> {mc.max_version} "
          f"({len(mc.versions)}): {', '.join(mc.versions)}")

    compatible = build_compatible(mc, mods)
    total = len(by_project)                          # distinct projects, not list lines
    print(f"\nCompatible mods per MC version (of {total} found):")
    for v, ps in compatible.items():
        print(f"  {v}: {len(ps)}/{total}  {[p.title for p in ps]}")

    top = top_versions(compatible, 1 if only else 3)
    if not top:
        print("\nNo version has a compatible mod in the range:")
        explain_misses([p for ps in mods.values() for p in ps], mc)
        return

    print(f"\nSelected version {only}:" if only else "\nBest versions (most supported mods, then newest):")
    for rank, v in enumerate(top, 1):
        print(f"  {rank}. MC {v}: {len(compatible[v])}/{total} mod(s)")

    ok = {p.title.lower() for p in compatible[top[0]]}
    missing = [n for n, ps in mods.items() if ps and not any(p.title.lower() in ok for p in ps)]
    if missing:
        print(f"Missing on {top[0]}: " + ", ".join(missing))

    if do_export:
        files = export_markdown(compatible, out_dir, prefix, n=1 if only else 3)
        print("\nWrote:")
        for f in files:
            print(f"  {f}")

if __name__ == "__main__":
    main()