"""Reading the mod list and writing the exported markdown files."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

from ..models.classes import Project, top_versions
from .md_render import render_all, render_markdown


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


def export_all(projects: list[Project], out_dir: Path, prefix: str = "MC_Update") -> Path:
    """Write <prefix>_ALL.md with every found project; an existing file is replaced."""
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{prefix}_ALL.md"
    fd, tmp = tempfile.mkstemp(dir=out_dir, suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
        f.write(render_all(projects))
    os.replace(tmp, path)
    return path
