"""Reading the mod list and writing the exported markdown files."""

from __future__ import annotations

import os
import tempfile
import questionary
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


def export_all(projects: list[Project], out_dir: Path, prefix: str = "MC_Update",
               known: list[str] | None = None) -> Path:
    """Write <prefix>_ALL.md with every found project; an existing file is replaced."""
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{prefix}_ALL.md"
    fd, tmp = tempfile.mkstemp(dir=out_dir, suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
        f.write(render_all(projects, known))
    os.replace(tmp, path)
    return path



def searchMatch(PATH, file_ending=None, prompt="Search", directories=False):
    PATH = Path(PATH)

    if not PATH.is_dir():
        print(f"Folder does not exist: {PATH}")
        return None

    search = input(f"{prompt}: ").strip().lower()

    matches = [
        item
        for item in PATH.iterdir()
        if (
            item.is_dir() if directories
            else (
                item.is_file()
                and (
                    file_ending is None
                    or item.suffix.lower() == file_ending.lower()
                )
            )
        )
        and search in item.name.lower()
    ]

    matches.sort(key=lambda item: item.name.lower())

    if not matches:
        print("No matches found.")
        return None

    if len(matches) > 100:
        print(f"Found {len(matches)} matches. Showing first 100.")

    selected = questionary.select(
        "Select:",
        choices=[item.name for item in matches[:100]]
    ).ask()

    if selected is None:
        return None

    return PATH / selected


def selectWithRetry(
    PATH,
    file_ending=None,
    prompt="Search",
    directories=False,
    attempts=3
):
    for attempt in range(1, attempts + 1):
        print(f"\nAttempt {attempt}/{attempts}")

        selected = searchMatch(
            PATH,
            file_ending=file_ending,
            prompt=prompt,
            directories=directories
        )

        if selected is not None:
            return selected

    print(f"{attempts} unsuccessful attempts. Aborting.")
    return None
    
    
def initMarkdown(MK_FOLDER):
    print(
        f"Markdown Base-path: {MK_FOLDER}\n"
        "Select Markdown file:"
    )

    return selectWithRetry(
        MK_FOLDER,
        file_ending=".md",
        prompt="Search Markdown"
    )

    # USAGE:
    # base_path = Path(_opt_or_none("file") or require_env("MOD_LIST_PATH"))
    #     path = initMarkdown(base_path)
    #     if path is None:
    #         return