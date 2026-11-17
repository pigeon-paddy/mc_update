"""Data classes and the pure compatibility logic (no API or file access, so nothing circular)."""

from __future__ import annotations

import json
from dataclasses import asdict
from dataclasses import dataclass
from dataclasses import field
from dataclasses import fields

from ..config.default_config import RELEASE_RE, WILDCARD_RE


# --------------------------------------------------------------------------- #
# Data classes
# --------------------------------------------------------------------------- #
def version_key(v: str) -> tuple[int, ...]:
    """
    Sortable key for release versions. Works across the 2026 scheme change:
        1.21.1 -> (1, 21, 1)  <  1.21.11 -> (1, 21, 11)  <  26.1 -> (26, 1)  <  26.1.1 -> (26, 1, 1)
    (old scheme is 1.<major>.<patch>, new scheme is <year>.<drop>.<hotfix>)
    """
    return tuple(WILDCARD_SORT if p.lower() in ("x", "*") else int(p) for p in v.split("."))


WILDCARD_SORT = 10**6          # "1.21.x" sorts after every real 1.21.N


def normalize_version_tag(tag: str) -> str | None:
    """'1.21.X' / '1.21.*' -> '1.21.x'; a plain release stays as is; anything else (snapshots, ...) -> None."""
    tag = tag.strip()
    m = WILDCARD_RE.match(tag)
    if m:
        return f"{m.group(1)}.x"
    return tag if RELEASE_RE.match(tag) else None


def is_wildcard(tag: str) -> bool:
    return tag.endswith(".x")


def series_matches(tag: str, version: str) -> bool:
    """Does the tag cover this version? Exact for releases; '1.21.x' covers 1.21 (=1.21.0), 1.21.1, ... 1.21.N."""
    if not is_wildcard(tag):
        return tag == version
    series = tag[:-2]                                    # '1.21'
    return version == series or version.startswith(series + ".")


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

    def has_version(self, mc_version: str) -> bool:
        """Supported on this release, either listed explicitly or covered by a wildcard like '1.21.x'."""
        return any(series_matches(tag, mc_version) for tag in self.version_loaders)

    def loaders_on(self, mc_version: str) -> set[str]:
        """Loaders supporting this release (union over the explicit entry and matching wildcard entries)."""
        out: set[str] = set()
        for tag, loaders in self.version_loaders.items():
            if series_matches(tag, mc_version):
                out |= {l.lower() for l in loaders}
        return out

    def supports(self, mc_version: str) -> bool:
        return self.has_version(mc_version)

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


@dataclass
class MC:
    loader: str | None          # None = any loader (compare them in the exported table)
    versions: list[str]          # newest -> oldest
    min_version: str
    max_version: str

    def target_versions(self) -> list[str]:
        """Configured versions, newest -> oldest, clipped to [min, max]."""
        lo, hi = version_key(self.min_version), version_key(self.max_version)
        return [v for v in sorted(self.versions, key=version_key, reverse=True)
                if lo <= version_key(v) <= hi]


def supports_on(p: Project, version: str, loader: str | None = None) -> bool:
    """MC version supported (and, for mods, the given loader if one is requested)."""
    if not p.has_version(version):
        return False
    if loader is None or p.project_type != "mod":          # datapacks/plugins/resource packs: version only
        return True
    loaders = p.loaders_on(version)
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
