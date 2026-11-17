"""Static settings and .env handling. Everything else imports its constants from here."""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path


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


# --------------------------------------------------------------------------- #
# Constants (everything below may read the environment, so it must stay after load_env(...))
# --------------------------------------------------------------------------- #
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

MC_VERSIONS_TTL = 6 * 3600   # list of MC releases: 6 h

DEFAULT_MIN_VERSION = "1.21.11"

RELEASE_RE = re.compile(r"^\d+\.\d+(\.\d+)?$")  # skips snapshots / pre-releases
# A project tag like "1.21.x" (or 1.21.*) means: every release of that series, 1.21.0 up to the last 1.21.N
WILDCARD_RE = re.compile(r"^(\d+\.\d+)\.[xX*]$")

# Modrinth lists loaders (fabric, forge, ...) inside "categories"; they are not real categories
LOADER_TAGS = {"fabric", "forge", "neoforge", "quilt", "liteloader", "rift", "modloader",
               "bukkit", "spigot", "paper", "purpur", "folia", "sponge", "bungeecord",
               "velocity", "waterfall", "datapack", "minecraft"}

# Export layout: heading -> ordered sub-categories. Each sub-category is (display name, alias, alias, ...).
# Aliases cover Modrinth tags and CurseForge category names (matched ignoring case/punctuation).
# Mods are sorted by sub-category in the order listed here, then by title; no sub-headings are written.
# Edit freely - anything that matches nothing ends up under "Other".
CATEGORY_GROUPS: dict[str, list[tuple[str, ...]]] = {
    "World Generation": [
        ("World generation", "worldgen", "world gen", "world generation"),
    ],
    "World Exploration": [
            ("Adventure", "adventure", "adventure and rpg"),
            ("Mobs", "mobs"),
            ("Map & information", "map and information", "information"),
    ],
    "Gameplay": [
        ("Game mechanics", "game-mechanics", "game mechanics"),
        ("Magic", "magic"),
        ("Equipment", "equipment", "armor, tools, and weapons"),
        ("Food", "food", "food and cooking"),
    ],
    "Technology & Automation": [
            ("Technology", "technology"),
            ("Redstone", "redstone"),
            ("Storage", "storage"),
            ("Transportation", "transportation"),
    ],
    "Visuals & Quality of Life": [
        ("Decoration", "decoration"),
        ("Cosmetic", "cosmetic"),
        ("Utility", "utility", "utility & qol", "qol"),
        ("Social", "social"),
        ("Education", "education"),
    ],
    "Performance & Server": [
            ("Optimization", "optimization", "performance"),
            ("Server", "server utility", "server", "management"),
            ("Library", "library", "library / api", "api and library"),
            ("Economy", "economy"),
            ("Minigame", "minigame"),
            ("Cursed", "cursed"),
        ],
    "Other": [
        ("Miscellaneous", "miscellaneous"),
    ],
}

FALLBACK_HEADING = "Other"

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