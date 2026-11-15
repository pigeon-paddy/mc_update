# mc-update

Looks up the mods in a markdown list on Modrinth (and CurseForge), finds the Minecraft versions that
fit most of them and writes per-version markdown files (`<list>_<version>.md`, optionally `<list>_ALL.md`).

## Setup

    uv sync
    cp .env.example .env        # fill in USER_AGENT, MOD_LIST_PATH, CURSEFORGE_API_KEY

## Usage

    uv run mc-update                      # read the list from MOD_LIST_PATH
    uv run mc-update Dreamshift           # test a single mod
    uv run mc-update --version=1.21.1     # one fixed MC version, no best-fit ranking
    uv run mc-update --all                # also write <list>_ALL.md
    uv run python -m mc_update            # same thing as mc-update

Flags: `--loader= --min= --max= --version= --refresh --verbose --all --no-export --export --file=`

The `.env` is read from the folder you start the command in (and from `src/`); real environment
variables take precedence.
