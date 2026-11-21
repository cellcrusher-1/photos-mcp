"""Photos connector for Claude: read-only search of Apple Photos, matching photos to a
route by location, and small JPEG previews written to ~/Pictures/Claude Exports.

There is deliberately no tool that runs arbitrary osxphotos commands, changes the
library, or writes anywhere else.
"""
from __future__ import annotations

from collections import Counter

from mcp.server.mcpserver import MCPServer

from . import geo, library as lib

server = MCPServer(
    "photos",
    instructions=(
        "Read-only access to Christopher's Apple Photos library on his Mac. Search returns "
        "details (dates, places, people), not images. Use match_to_route to sort photos onto "
        "legs or stops by location, and export_previews to write small JPEGs for a shortlist."
    ),
)


def _filter(from_date=None, to_date=None, text=None, album=None, person=None, keyword=None,
            place=None, favorite=None, media_type=None, has_location=None):
    f, t = lib.parse_date(from_date), lib.parse_date(to_date, end=True)
    words = [w.lower() for w in (text or "").split() if w]
    out = []
    for p in lib.visible_photos():
        d = lib.local_naive(p.date)
        if (f and d < f) or (t and d > t):
            continue
        if media_type == "photo" and p.ismovie or media_type == "video" and not p.ismovie:
            continue
        if favorite is not None and bool(p.favorite) != favorite:
            continue
        loc = lib.location(p)
        if has_location is not None and (loc is not None) != has_location:
            continue
        if album and album.lower() not in (a.lower() for a in p.albums):
            continue
        if person and person.lower() not in (x.lower() for x in p.persons):
            continue
        if keyword and keyword.lower() not in (k.lower() for k in p.keywords):
            continue
        pname = (p.place.name if p.place else "") or ""
        if place and place.lower() not in pname.lower():
            continue
        if words:
            hay = " ".join([p.title or "", p.description or "", p.original_filename or "", pname,
                            *p.persons, *p.keywords, *p.albums]).lower()
            if not all(w in hay for w in words):
                continue
        out.append(p)
    out.sort(key=lambda p: lib.local_naive(p.date), reverse=True)
    return out


@server.tool()
def search_photos(from_date: str | None = None, to_date: str | None = None, text: str | None = None,
                  album: str | None = None, person: str | None = None, keyword: str | None = None,
                  place: str | None = None, favorite: bool | None = None,
                  media_type: str | None = None, has_location: bool | None = None,
                  limit: int = 50, offset: int = 0) -> dict:
    """Find photos and videos, newest first. Every filter is optional; they combine.
    Dates are YYYY-MM-DD (or with a time) in the photo's own local time; to_date includes
    that whole day. media_type is "photo" or "video". has_location true/false keeps only
    photos with/without GPS. Returns totals plus up to `limit` (max 200) matches."""
    hits = _filter(from_date, to_date, text, album, person, keyword, place, favorite, media_type, has_location)
    limit = max(1, min(int(limit), 200))
    offset = max(0, int(offset))
    return {
        "total": len(hits),
        "with_location": sum(1 for p in hits if lib.location(p)),
        "by_day": dict(sorted(Counter(lib.local_naive(p.date).date().isoformat() for p in hits).items())),
        "photos": [lib.brief(p) for p in hits[offset:offset + limit]],
    }


@server.tool()
def get_photo(uuid: str) -> dict:
    """Full details for one photo: albums, people, keywords, title, size, location."""
    return lib.full(lib.by_uuid(uuid))


@server.tool()
def list_albums() -> list[dict]:
    """Every album (including shared ones) with its folder and photo count."""
    d = lib.db()
    out = []
    for a in list(d.album_info) + list(getattr(d, "album_info_shared", []) or []):
        out.append({"album": a.title, "folder": "/".join(getattr(a, "folder_names", []) or []),
                    "count": len([p for p in a.photos if not p.hidden])})
    return sorted(out, key=lambda x: (x["folder"], x["album"] or ""))


@server.tool()
def list_people_and_keywords() -> dict:
    """Named people and keywords in the library, with photo counts."""
    d = lib.db()
    people = {k: v for k, v in d.persons_as_dict.items() if k and not k.startswith("_")}
    return {"people": dict(sorted(people.items(), key=lambda kv: -kv[1])),
            "keywords": dict(sorted(d.keywords_as_dict.items(), key=lambda kv: -kv[1]))}


@server.tool()
def match_to_route(points: list[dict], from_date: str | None = None, to_date: str | None = None,
                   mode: str = "segments", max_distance_mi: float = 1.0,
                   per_stop_limit: int = 40) -> dict:
    """Sort located photos onto a route. points is an ordered list of {name, lat, lon}
    (for example relay exchanges or trip stops). mode "segments": photo goes to the
    nearest stretch between consecutive points (segment i = points[i] → points[i+1]);
    mode "points": to the nearest point. Photos farther than max_distance_mi from the
    route are counted as off_route. Returns, per segment or point, the count and up to
    per_stop_limit photos (closest first)."""
    pts = geo.clean_points(points)
    if mode not in ("segments", "points"):
        raise ValueError('mode is "segments" or "points"')
    if mode == "segments" and len(pts) < 2:
        raise ValueError("segments mode needs at least 2 points")
    max_d = max(0.01, min(float(max_distance_mi), 50.0))
    per = max(1, min(int(per_stop_limit), 100))
    hits = _filter(from_date, to_date, media_type="photo", has_location=True)
    groups: dict[int, list] = {}
    off = 0
    for p in hits:
        lat, lon = lib.location(p)
        i, d = geo.nearest(lat, lon, pts, mode)
        if d > max_d:
            off += 1
            continue
        groups.setdefault(i, []).append((d, p))
    stops = []
    n = len(pts) - 1 if mode == "segments" else len(pts)
    for i in range(n):
        g = sorted(groups.get(i, []), key=lambda x: x[0])
        name = f"{pts[i]['name']} → {pts[i + 1]['name']}" if mode == "segments" else pts[i]["name"]
        stops.append({"index": i, "name": name, "count": len(g),
                      "photos": [dict(lib.brief(p), miles_off=round(d, 2)) for d, p in g[:per]]})
    return {"photos_considered": len(hits), "matched": len(hits) - off, "off_route": off, "stops": stops}


@server.tool()
def export_previews(uuids: list[str], folder: str, max_px: int = 1024) -> dict:
    """Write small JPEG previews (longest side max_px, 200–2048) of up to 100 photos into
    ~/Pictures/Claude Exports/<folder>/. Never changes the library or the originals.
    folder: letters, numbers, spaces, - and _ only."""
    if not isinstance(uuids, list) or not (1 <= len(uuids) <= 100):
        raise ValueError("uuids: 1 to 100 photo ids")
    px = max(200, min(int(max_px), 2048))
    results = []
    for u in uuids:
        try:
            results.append(lib.export_preview(lib.by_uuid(u), folder, px))
        except ValueError as e:
            results.append({"uuid": u, "ok": False, "why": str(e)})
    ok = [r for r in results if r.get("ok")]
    return {"folder": str(lib.EXPORT_ROOT / folder), "written": len(ok),
            "bytes": sum(r["bytes"] for r in ok), "results": results}


def main() -> None:
    server.run("stdio")


if __name__ == "__main__":
    main()
