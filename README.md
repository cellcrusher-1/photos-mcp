# photos-mcp

A small, **read-only** Apple Photos connector for Claude (desktop app and Claude Code),
built on [osxphotos](https://github.com/RhetTbull/osxphotos). Replaces the third-party
`osxphotos-mcp`, which fetched unpinned code and could run any osxphotos command.

## Tools

| Tool | What it does |
| --- | --- |
| `search_photos` | Filter by date, text, album, person, keyword, place, favorite, photo/video, has GPS. Returns details, counts per day, never images. |
| `get_photo` | Full details for one photo. |
| `list_albums` | Albums with folder and count. |
| `list_people_and_keywords` | Named people and keywords with counts. |
| `match_to_route` | Sort located photos onto an ordered list of points (relay exchanges, trip stops): nearest segment or nearest point. |
| `export_previews` | Small JPEGs (≤2048 px) of up to 100 photos into `~/Pictures/Claude Exports/<folder>/`. |

## What it can't do (by design)

- Change, delete, tag or move anything in the library.
- Run arbitrary osxphotos commands.
- Write anywhere except `~/Pictures/Claude Exports/`.
- Return hidden photos or Recently Deleted.
- Make network connections: this code never calls the internet (only the Claude app talks to Anthropic).

## Privacy

Runs on the Mac. Search results (dates, places, file names, people names) go to Claude
as part of the chat. Images leave the Mac only if Claude opens an exported preview or
you move one somewhere.

## Install / update

1. uv installed (`uvx --version` works).
2. `git clone https://github.com/cellcrusher-1/photos-mcp.git ~/photos-mcp && cd ~/photos-mcp && ./install.sh`
3. Add the Python path it prints to System Settings → Privacy & Security → Full Disk
   Access. It is a Python used only by this connector (`~/.photos-mcp/python/...`).
4. Quit and reopen Claude.

Update later: `cd ~/photos-mcp && git pull && ./install.sh`. Versions are pinned in
`pyproject.toml` (mcp 2.3.0, osxphotos 0.78.1); bump them only on purpose.

## Tests

`PYTHONPATH=src python3 -m pytest -q tests` (runs anywhere; Photos access is faked).
