"""Read-only access to the Apple Photos library through osxphotos.

Nothing here changes the library: osxphotos reads its database (from a temporary
copy when Photos has it open). Hidden photos and Recently Deleted are never returned.
"""
from __future__ import annotations

import datetime as dt
import os
import pathlib
import re
import shutil
import subprocess
import threading
import time

DEFAULT_LIBRARY = pathlib.Path.home() / "Pictures" / "Photos Library.photoslibrary"
EXPORT_ROOT = pathlib.Path.home() / "Pictures" / "Claude Exports"
RELOAD_AFTER_S = 600  # re-read the library if the copy in memory is older than 10 min

_lock = threading.Lock()
_db = None
_loaded_at = 0.0


def library_path() -> pathlib.Path:
    p = os.environ.get("PHOTOS_MCP_LIBRARY")
    return pathlib.Path(p).expanduser() if p else DEFAULT_LIBRARY


def db():
    """The PhotosDB, loaded once and refreshed every 10 minutes."""
    global _db, _loaded_at
    with _lock:
        if _db is None or time.time() - _loaded_at > RELOAD_AFTER_S:
            import osxphotos  # imported late so the server starts fast and tests run off-Mac

            lib = library_path()
            if not lib.exists():
                raise RuntimeError(f"Photos library not found at {lib}. Set PHOTOS_MCP_LIBRARY to its path.")
            try:
                _db = osxphotos.PhotosDB(dbfile=str(lib))
            except PermissionError as e:
                raise RuntimeError(
                    "macOS blocked reading the Photos library. Give Full Disk Access to this "
                    "connector's Python (see README), then restart the Claude app."
                ) from e
            _loaded_at = time.time()
        return _db


def visible_photos():
    return [p for p in db().photos(intrash=False) if not p.hidden]


def local_naive(d: dt.datetime) -> dt.datetime:
    """The photo's own local clock time, without a time zone."""
    return d.replace(tzinfo=None)


def parse_date(s: str | None, end: bool = False) -> dt.datetime | None:
    if not s:
        return None
    s = s.strip()
    try:
        d = dt.datetime.fromisoformat(s)
    except ValueError:
        raise ValueError(f"Dates are YYYY-MM-DD or YYYY-MM-DDTHH:MM, got {s!r}")
    if end and len(s) == 10:
        d = d + dt.timedelta(days=1) - dt.timedelta(microseconds=1)
    return d.replace(tzinfo=None)


def location(p) -> tuple[float, float] | None:
    lat, lon = p.location
    if lat is None or lon is None:
        return None
    return float(lat), float(lon)


def brief(p) -> dict:
    loc = location(p)
    place = p.place.name if p.place else None
    return {
        "uuid": p.uuid,
        "date": local_naive(p.date).isoformat(timespec="seconds"),
        "type": "video" if p.ismovie else "photo",
        "filename": p.original_filename,
        "lat": round(loc[0], 6) if loc else None,
        "lon": round(loc[1], 6) if loc else None,
        "place": place,
        "favorite": bool(p.favorite),
    }


def full(p) -> dict:
    d = brief(p)
    d.update(
        {
            "title": p.title,
            "description": p.description,
            "albums": sorted(p.albums),
            "people": sorted(x for x in p.persons if x and not x.startswith("_")),
            "keywords": sorted(p.keywords),
            "width": p.width,
            "height": p.height,
            "on_this_mac": bool(p.path) or bool(p.path_derivatives),
        }
    )
    return d


def by_uuid(uuid: str):
    if not re.fullmatch(r"[0-9A-Fa-f-]{8,40}", uuid or ""):
        raise ValueError("Not a photo id")
    hits = [p for p in db().photos(uuid=[uuid], intrash=False) if not p.hidden]
    if not hits:
        raise ValueError(f"No photo {uuid}")
    return hits[0]


SAFE_FOLDER = re.compile(r"^[A-Za-z0-9 _-]{1,60}$")


def export_preview(p, folder: str, max_px: int) -> dict:
    """Write one JPEG preview (longest side max_px) into ~/Pictures/Claude Exports/<folder>/.
    Uses Photos' own preview when there is one, so iCloud originals aren't downloaded."""
    if not SAFE_FOLDER.match(folder or ""):
        raise ValueError("folder may only use letters, numbers, spaces, - and _ (max 60)")
    dest_dir = EXPORT_ROOT / folder
    dest_dir.mkdir(parents=True, exist_ok=True)
    srcs = [s for s in (p.path_derivatives or []) if s and os.path.exists(s)]
    src = srcs[0] if srcs else (p.path if p.path and os.path.exists(p.path) else None)
    if not src or p.ismovie:
        return {"uuid": p.uuid, "ok": False, "why": "video" if p.ismovie else "not on this Mac (iCloud only)"}
    stamp = local_naive(p.date).strftime("%Y%m%d-%H%M%S")
    out = dest_dir / f"{stamp}_{p.uuid[:8]}.jpg"
    sips = shutil.which("sips") or "/usr/bin/sips"
    r = subprocess.run(
        [sips, "-s", "format", "jpeg", "-s", "formatOptions", "80", "-Z", str(max_px), src, "--out", str(out)],
        capture_output=True, text=True, timeout=60,
    )
    if r.returncode != 0 or not out.exists():
        return {"uuid": p.uuid, "ok": False, "why": (r.stderr or "sips failed").strip()[:200]}
    return {"uuid": p.uuid, "ok": True, "path": str(out), "bytes": out.stat().st_size}
