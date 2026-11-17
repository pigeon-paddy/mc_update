"""CurseForge API client."""

from __future__ import annotations

import requests

from ..config.default_config import (
    CF_CLASS_IDS,
    CF_GAME_ID_MINECRAFT,
    CF_LOADERS,
    CURSEFORGE_API,
    TIMEOUT,
    require_env,
    user_agent,
)
from ..models.classes import Project, normalize_version_tag, version_key


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
        tag = normalize_version_tag(i["gameVersion"])      # keeps wildcards like "1.21.x"
        if not tag:
            continue
        ml = i.get("modLoader")
        if project_type == "plugin":
            name = "bukkit"                               # CurseForge class 5 = Bukkit plugins
        elif ml is None:
            name = "any"                                  # no loader info (e.g. libraries, packs)
        else:
            name = CF_LOADERS.get(ml, "unknown")
        vl.setdefault(tag, set()).add(name)
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
