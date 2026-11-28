import datetime as dt, importlib.util, json, pathlib
from types import SimpleNamespace as NS

spec = importlib.util.spec_from_file_location("ks", pathlib.Path(__file__).parent.parent / "extras/kitchen_sync/kitchen_sync.py")
ks = importlib.util.module_from_spec(spec); spec.loader.exec_module(ks)

def ph(uuid, day, **kw):
    d = dict(uuid=uuid, date=dt.datetime(2026, 11, day), hidden=False, intrash=False, ismovie=False); d.update(kw)
    return NS(**d)

def test_ids_are_hashes_not_uuids():
    i = ks.photo_id("abcd-1234")
    assert ks.ID_RE.fullmatch(i) and "abcd" not in i and i == ks.photo_id("ABCD-1234")

def test_album_skips_hidden_trash_video_and_caps(monkeypatch):
    from photos_mcp import library as lib
    photos = [ph("A", 1), ph("B", 2, hidden=True), ph("C", 3, ismovie=True), ph("D", 4, intrash=True), ph("E", 5)]
    db = NS(album_info=[NS(title="Kitchen", photos=photos + [photos[0]]), NS(title="Other", photos=[ph("Z", 9)])])
    monkeypatch.setattr(lib, "db", lambda: db)
    out, skipped = ks.album_photos("Kitchen")
    assert [p.uuid for p in out] == ["E", "A"] and skipped == 3
    monkeypatch.setattr(ks, "MAX_PHOTOS", 1)
    out, skipped = ks.album_photos("Kitchen")
    assert [p.uuid for p in out] == ["E"] and skipped == 4

def test_missing_album_stops_before_anything_is_removed(monkeypatch):
    from photos_mcp import library as lib
    monkeypatch.setattr(lib, "db", lambda: NS(album_info=[]))
    try:
        ks.album_photos("Kitchen"); assert False
    except SystemExit as e:
        assert "No album" in str(e)

def test_sync_sends_new_removes_gone_and_reports(monkeypatch):
    from photos_mcp import library as lib
    keep, new = ph("KEEP", 1), ph("NEW", 2)
    monkeypatch.setattr(lib, "db", lambda: NS(album_info=[NS(title="Kitchen", photos=[keep, new])]))
    monkeypatch.setattr(ks, "load_config", lambda: ("base.example.dev", "cid", "s" * 20, "Kitchen"))
    monkeypatch.setattr(ks, "preview_bytes", lambda p: (b"\xff\xd8jpeg", None))
    monkeypatch.setattr(ks, "clean_work_folder", lambda: None)
    calls = []
    gone = "f" * 32
    class FakePage:
        def __init__(self, *a): pass
        def ids(self): return {ks.photo_id("KEEP"), gone}
        def call(self, m, path="", body=None, ctype=None):
            calls.append((m, path, body)); return 200, b"{}"
    monkeypatch.setattr(ks, "Page", FakePage)
    assert ks.sync(check=True) == 0 and calls == []
    assert ks.sync(check=False) == 0
    assert ("PUT", "/" + ks.photo_id("NEW"), b"\xff\xd8jpeg") in calls
    assert ("DELETE", "/" + gone, None) in calls
    done = json.loads(calls[-1][2])
    assert calls[-1][1] == "/done" and set(done["ids"]) == {ks.photo_id("KEEP"), ks.photo_id("NEW")} and done["skipped"] == 0

def test_setup_rejects_bad_codes(monkeypatch, tmp_path):
    monkeypatch.setattr(ks, "CONFIG", tmp_path / "k.json")
    for code in ["nope", "kitchen-sync:!!!", "kitchen-sync:" + __import__("base64").b64encode(b'{"host":"evil/x","client_id":"a","client_secret":"bbbbbbbbbbbbbbbbbb"}').decode()]:
        monkeypatch.setattr(ks.getpass, "getpass", lambda *_: code)
        assert ks.setup() == 1
    assert not (tmp_path / "k.json").exists()
