"""Modrinth API client."""

from __future__ import annotations

import json
import requests

from ..config.default_config import (
    LOADER_TAGS,
    MODRINTH_API,
    RELEASE_RE,
    TIMEOUT,
    user_agent,
)
from ..models.classes import Project, version_key


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
# Your MC config + compatibility / best-fit logic (from the sketch)
# --------------------------------------------------------------------------- #
def fetch_release_versions() -> list[str]:
    """All Minecraft *release* versions known to Modrinth (excludes snapshots, pre-releases, RCs, betas)."""
    r = requests.get(f"{MODRINTH_API}/tag/game_version",
                     headers={"User-Agent": user_agent()}, timeout=TIMEOUT)
    r.raise_for_status()
    return [v["version"] for v in r.json()
            if v.get("version_type") == "release" and RELEASE_RE.match(v["version"])]
