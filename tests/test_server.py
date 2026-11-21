import datetime as dt
from types import SimpleNamespace as NS
from photos_mcp import server, library as lib

TZ = dt.timezone(dt.timedelta(hours=-7))

def ph(uuid, when, lat=None, lon=None, **kw):
    d = dict(uuid=uuid, date=dt.datetime.fromisoformat(when).replace(tzinfo=TZ), ismovie=False, hidden=False,
             favorite=False, location=(lat, lon), place=NS(name="Sandy, OR") if lat else None,
             original_filename=uuid + ".HEIC", title=None, description=None, albums=["HTC"],
             persons=["Jill", "_UNKNOWN_"], keywords=[], width=4000, height=3000, path=None, path_derivatives=[])
    d.update(kw); return NS(**d)

PHOTOS = [
    ph("AAAAAAAA-1", "2026-08-28T13:00:00", 45.43, -122.284),   # on leg Sandy→Proctor
    ph("BBBBBBBB-2", "2026-08-28T14:00:00", 45.44, -122.33),    # near Proctor→Dee
    ph("CCCCCCCC-3", "2026-08-28T15:00:00", 47.42, -120.31, place=NS(name="Wenatchee, WA")),    # Wenatchee: off route
    ph("DDDDDDDD-4", "2026-08-28T16:00:00"),                    # no location
    ph("EEEEEEEE-5", "2026-07-01T10:00:00", 45.43, -122.284),   # outside dates
]

def setup_module():
    lib.visible_photos = lambda: PHOTOS

def test_search_dates_and_location():
    r = server.search_photos(from_date="2026-08-28", to_date="2026-08-28")
    assert r["total"] == 4 and r["with_location"] == 3 and r["by_day"] == {"2026-08-28": 4}
    assert r["photos"][0]["uuid"] == "DDDDDDDD-4"  # newest first
    assert server.search_photos(text="sandy jill", from_date="2026-08-01")["total"] == 2

def test_match_to_route():
    pts = [{"name": "Ex6", "lat": 45.405721, "lon": -122.276866},
           {"name": "Ex7", "lat": 45.453705, "lon": -122.290537},
           {"name": "Ex8", "lat": 45.43217, "lon": -122.375395}]
    r = server.match_to_route(pts, "2026-08-28", "2026-08-28")
    assert r["photos_considered"] == 3 and r["matched"] == 2 and r["off_route"] == 1
    assert [s["count"] for s in r["stops"]] == [1, 1]
    assert r["stops"][0]["name"] == "Ex6 → Ex7"

def test_export_rejects_bad_folder():
    import pytest
    with pytest.raises(ValueError):
        lib.export_preview(PHOTOS[0], "../etc", 800)
