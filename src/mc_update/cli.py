"""
Look up mods on Modrinth / CurseForge, find the best Minecraft version, export markdown.

Setup:  copy .env.example to .env (USER_AGENT, MOD_LIST_PATH, CURSEFORGE_API_KEY).
Usage:  mc-update                 # read the mod list from MOD_LIST_PATH
        mc-update Dreamshift      # test a single mod
"""

from __future__ import annotations

import os
import sys
import socket
from collections.abc import Callable
from pathlib import Path

from .clients.api_curseforge import curseforge_get
from .clients.api_modrinth import fetch_release_versions, modrinth_get
from .config.default_config import (
    DEFAULT_MIN_VERSION,
    MC_VERSIONS_TTL,
    SEARCH_TYPES,
    require_env,
)
from .models.cache import ProjectCache
from .models.classes import (
    MC,
    Project,
    build_compatible,
    series_matches,
    supports_on,
    top_versions,
    version_key,
)
from .parsers.file_handler import export_all, export_markdown, export_wiki, read_mod_list, initMarkdown


# --------------------------------------------------------------------------- #
# Combined lookup + demo
# --------------------------------------------------------------------------- #
def lookup(name: str, cache: ProjectCache | None = None, refresh: bool = False, Debug=False) -> list[Project]:
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
                if ptype != "mod" and Debug:
                    print(f"[lookup] {name!r} is not a mod - found as {ptype} on {source}", file=sys.stderr)
                return [p]

    print(f"[lookup] nothing found for {name!r} (searched: {', '.join(SEARCH_TYPES)})", file=sys.stderr)
    return []


def mc_versions_between(min_version: str = DEFAULT_MIN_VERSION,
                        max_version: str | None = None,
                        cache: ProjectCache | None = None,
                        refresh: bool = False) -> list[str]:
    """
    Release versions from min_version up to max_version (None = newest available),
    returned newest -> oldest. Handles 1.21.11 -> 26.1 and later (26.1.1, 26.2, 26.3, ...).
    """
    cache = cache or ProjectCache()
    all_versions = cache.fetch_value("_meta:mc_release_versions", fetch_release_versions,
                                     MC_VERSIONS_TTL, refresh=refresh)
    lo = version_key(min_version)
    hi = version_key(max_version) if max_version else None
    return sorted(
        (v for v in all_versions
         if version_key(v) >= lo and (hi is None or version_key(v) <= hi)),
        key=version_key, reverse=True,
    )


def build_mc(loader: str | None, min_version: str = DEFAULT_MIN_VERSION,
             max_version: str | None = None, cache: ProjectCache | None = None,
             refresh: bool = False) -> MC:
    """MC config from the API: all releases between min and max (max=None -> newest)."""
    versions = mc_versions_between(min_version, max_version, cache, refresh)
    if not versions:
        raise ValueError(f"no Minecraft releases found between {min_version} and {max_version or 'newest'}")
    return MC(loader, versions, min_version, versions[0])   # max_version resolved to newest


def explain_misses(projects: list[Project], mc: MC) -> None:
    """Say *why* a project is not compatible (wrong loader vs. version outside the range)."""
    targets = mc.target_versions()                         # releases inside the configured range
    for p in projects:
        in_range = [v for v in targets if p.has_version(v)]    # wildcards like 1.21.x count for every 1.21.N
        outside = [t for t in p.versions if not any(series_matches(t, v) for v in targets)]
        print(f"  {p.title} [{p.source}, {p.project_type}] loaders={p.modloader or '-'} versions={p.versions or '-'}")
        if not in_range:
            print(f"    x no supported version inside {mc.min_version} .. {mc.max_version}"
                  + (f" (only outside the range: {', '.join(outside)})" if outside else ""))
        elif mc.loader and not any(supports_on(p, v, mc.loader) for v in in_range):
            print(f"    x loader '{mc.loader}' not supported on any version in the range")


def _opt_or_none(name: str) -> str | None:
    for a in sys.argv[1:]:
        if a.startswith(f"--{name}="):
            return a.split("=", 1)[1]
    return None


def _opt(name: str, default: str) -> str:
    """Value of --name=VALUE, or default (always a str)."""
    return _opt_or_none(name) or default



import urllib.request

def check_internet_urllib(url="https://www.google.com", timeout=5):
    try:
        urllib.request.urlopen(url, timeout=timeout)
        return True
    except Exception as e:
        print(f"No internet connection: {e}")
        return False




def main() -> None:
    """
    Default:  read mods from MOD_LIST_PATH in .env (or --file=PATH) and compute the best-fitting MC version.
    Test:     mc-update "Mod Name"   -> look up just that one mod

    Flags (both modes): --loader=fabric (optional filter, default: all loaders)  --version=1.21.1 (one fixed MC version, skips the
    best-fit ranking)  --min=1.21.1  --max=26.3  --refresh  --verbose
    --all   also writes <list name>_ALL.md: every found project with its highest MC version + loaders
    --wiki  also writes <list name>_WIKI.md: every found project with a short description
    Export: list mode writes <list name>_<version>.md for the top 3 versions (--no-export to skip);
            test mode only does so with --export. Target folder: OUTPUT_DIR in .env, default = mod list folder.
    """
    
    if not check_internet_urllib():
        return
    
    positional = [a for a in sys.argv[1:] if not a.startswith("--")]
    refresh = "--refresh" in sys.argv
    verbose = "--verbose" in sys.argv
    list_path: Path | None = None

    if positional:                                   # test mode: single mod from the command line
        names = [" ".join(positional)]
        verbose = True                               # always show the full project info here
        do_export = "--export" in sys.argv           # test mode only writes files on request
    else:
        # path = Path(_opt_or_none("file") or require_env("MOD_LIST_PATH"))
        base_path = Path(_opt_or_none("file") or require_env("MOD_LIST_PATH"))
        path = initMarkdown(base_path)
        if path is None:
            return
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

    do_all, do_wiki = "--all" in sys.argv, "--wiki" in sys.argv
    everything: list[Project] = []                   # every found project once, independent of MC version
    if do_all or do_wiki:
        seen_ids: set[tuple] = set()
        for ps in mods.values():
            for p in ps:
                if (p.source, p.id) not in seen_ids:
                    seen_ids.add((p.source, p.id))
                    everything.append(p)

    if do_wiki:
        wiki_path = export_wiki(everything, out_dir, prefix)
        if wiki_path:
            print(f"\nWrote {wiki_path}  ({len(everything)} projects)")
        else:
            print("\nNothing found - wiki file not written.")

    if do_all:
        try:                                             # all known releases, to resolve wildcards like 1.21.x
            known = mc_versions_between("0.0", None, cache, refresh)
        except Exception as e:
            print(f"[mc versions] release list unavailable ({e}); wildcard versions stay unresolved", file=sys.stderr)
            known = []
        print(f"\nWrote {export_all(everything, out_dir, prefix, known)}  ({len(everything)} projects)")

    only = _opt_or_none("version")                   # --version=X: one fixed MC version, no best-fit ranking
    try:
        mc = build_mc(loader=_opt_or_none("loader"),
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
