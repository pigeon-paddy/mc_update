"""Markdown rendering: user view, deployment lists and the ALL file."""

from __future__ import annotations

import re

from ..config.default_config import (
    CATEGORY_GROUPS,
    DEPLOY_TYPES,
    FALLBACK_HEADING,
    LOADER_NAMES,
    LOADER_ORDER,
    LOADER_TAGS,
    NON_COLUMN_LOADERS,
    RESOLUTION_RE,
    SHOW_LOADER_SUMMARY,
    TYPE_ORDER,
    UNIVERSAL_TYPES,
)
from ..models.classes import Project, is_wildcard, series_matches, version_key


def _norm(text: str) -> str:
    return re.sub(r"[^a-z0-9]", "", text.lower())


# normalised alias -> (heading, position of the sub-category in the heading)
_CATEGORY_INDEX: dict[str, tuple[str, int]] = {
    _norm(alias): (heading, pos)
    for heading, subs in CATEGORY_GROUPS.items()
    for pos, sub in enumerate(subs)
    for alias in sub
}


def loader_columns(projects: list[Project], version: str) -> list[str]:
    """Loader columns for this file: every real loader any mod/plugin supports on this MC version."""
    cols: set[str] = set()
    for p in projects:
        if p.project_type not in UNIVERSAL_TYPES:
            cols |= p.loaders_on(version) - NON_COLUMN_LOADERS
    known = [l for l in LOADER_ORDER if l in cols]
    return known + sorted(cols - set(known))


def loader_cells(p: Project, version: str, columns: list[str]) -> set[str]:
    """Which loader columns get a mark for this project (a mod can fill several)."""
    if p.project_type in UNIVERSAL_TYPES:
        return set(columns)
    loaders = p.loaders_on(version)
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


def _sort_loaders(loaders: set[str]) -> list[str]:
    known = [l for l in LOADER_ORDER if l in loaders]
    return known + sorted(loaders - set(known))


def highest_version(p: Project, known: list[str] | None = None) -> str | None:
    """
    Highest MC release the project supports, whatever the configured range is.
    A wildcard like '1.21.x' resolves to the newest known 1.21.N (needs `known`, the list of all releases);
    without it the wildcard itself is returned.
    """
    candidates = {v for v in (known or []) if p.has_version(v)}       # known releases it covers
    for tag in p.version_loaders:
        # explicit entries always count; a wildcard only if no known release resolved it
        if not is_wildcard(tag) or not any(series_matches(tag, v) for v in candidates):
            candidates.add(tag)
    return max(candidates, key=version_key) if candidates else None


def _loader_text(p: Project, version: str | None) -> str:
    """Loaders supporting the project on that version, as display text."""
    if version is None:
        return "-"
    if p.project_type in UNIVERSAL_TYPES:
        return "all"                                       # datapacks / resource packs: every loader
    loaders = p.loaders_on(version)
    if "any" in loaders:
        return "any"
    shown = _sort_loaders(loaders - NON_COLUMN_LOADERS)
    return ", ".join(_loader_label(l) for l in shown) or "-"


def render_all(projects: list[Project], known: list[str] | None = None) -> str:
    """
    Every project, regardless of MC version: same headings, categories and sorting as the user view,
    but each row shows the highest supported MC version and the loaders that support it.
    """
    parts = []
    for type_heading, sections in _sections(projects):
        parts.append(f"# {type_heading}")
        for heading, entries in sections:
            lines = [_row(["Name", "Highest MC", "Loader"]), _row(["---", ":-:", "---"])]
            for p in entries:
                v = highest_version(p, known)
                lines.append(_row([_name_cell(p), v or "-", _loader_text(p, v)]))
            parts.append(f"## {heading}\n\n" + "\n".join(lines))
    return "\n\n".join(parts) + "\n"
