import datetime as dt, importlib.util, json, pathlib, random
from types import SimpleNamespace as NS

spec = importlib.util.spec_from_file_location("ks", pathlib.Path(__file__).parent.parent / "extras/kitchen_sync/kitchen_sync.py")
ks = importlib.util.module_from_spec(spec); spec.loader.exec_module(ks)

def ph(uuid, day=1, **kw):
    d = dict(uuid=uuid, date=dt.datetime(2026, 11, day), hidden=False, intrash=False, ismovie=False,
             isphoto=True, screenshot=False, shared=False, syndicated=False, shared_moment=False,
             burst=False, burst_key=False, burst_selected=False, uti="public.heic",
             width=4032, height=3024, labels=["Dog", "Outdoor"]); d.update(kw)
    return NS(**d)

def test_ids_are_hashes_not_uuids():
    i = ks.photo_id("abcd-1234")
    assert ks.ID_RE.fullmatch(i) and "abcd" not in i and i == ks.photo_id("ABCD-1234")

def test_filters_keep_paperwork_and_private_photos_off_the_wall():
    assert ks.why_not(ph("OK")) is None
    cases = {
        "hidden or deleted": [dict(hidden=True), dict(intrash=True)],
        "video": [dict(ismovie=True), dict(isphoto=False)],
        "screenshot": [dict(screenshot=True)],
        "shared with you": [dict(shared=True), dict(syndicated=True), dict(shared_moment=True)],
        "extra burst shot": [dict(burst=True)],
        "PNG or GIF": [dict(uti="public.png"), dict(uti="com.compuserve.gif")],
        "small image": [dict(width=640, height=480)],
        "document, receipt or text": [dict(labels=["Receipt"]), dict(labels=["Document", "Paper"]),
                                      dict(labels=["Text"]), dict(labels=["Credit Card"])],
    }
    for reason, kws in cases.items():
        for kw in kws:
            assert ks.why_not(ph("X", **kw)) == reason, (reason, kw)
    assert ks.why_not(ph("B", burst=True, burst_key=True)) is None
    assert ks.why_not(ph("S"), {"S"}) == "in an album you left out"

def test_labels_that_cant_be_read_dont_stop_the_photo():
    class NoLabels(NS):
        @property
        def labels(self):
            raise RuntimeError("no search info yet")
    d = vars(ph("L")); d.pop("labels")
    assert ks.why_not(NoLabels(**d)) is None

def test_pool_counts_reasons_and_skips_named_albums(monkeypatch):
    from photos_mcp import library as lib
    photos = [ph("A"), ph("B", screenshot=True), ph("C", labels=["Receipt"]), ph("D"), ph("E")]
    db = NS(album_info=[NS(title="Work", photos=[photos[3]])], photos=lambda intrash=False: photos)
    monkeypatch.setattr(lib, "db", lambda: db)
    pool, why = ks.library_pool(["Work"])
    assert [p.uuid for p in pool] == ["A", "E"]
    assert why == {"screenshot": 1, "document, receipt or text": 1, "in an album you left out": 1}

def test_empty_pool_stops_before_anything_is_removed(monkeypatch):
    from photos_mcp import library as lib
    monkeypatch.setattr(lib, "db", lambda: NS(album_info=[], photos=lambda intrash=False: [ph("A", hidden=True)]))
    try:
        ks.library_pool(); assert False
    except SystemExit as e:
        assert "nothing was changed" in str(e)

def test_pick_puts_photos_not_up_last_night_first():
    pool = [ph(c) for c in "ABCDEF"]
    have = {ks.photo_id("A"), ks.photo_id("B")}
    order = [p.uuid for p in ks.pick(pool, have, random.Random(1))]
    assert set(order[:4]) == set("CDEF") and set(order[4:]) == set("AB")

def _fake(monkeypatch, photos, have, preview=lambda p: (b"\xff\xd8jpeg", None)):
    from photos_mcp import library as lib
    monkeypatch.setattr(lib, "db", lambda: NS(album_info=[], photos=lambda intrash=False: photos))
    monkeypatch.setattr(ks, "load_config", lambda: ("base.example.dev", "cid", "s" * 20, []))
    monkeypatch.setattr(ks, "preview_bytes", preview)
    monkeypatch.setattr(ks, "clean_work_folder", lambda: None)
    calls = []
    class FakePage:
        def __init__(self, *a): pass
        def ids(self): return set(have)
        def call(self, m, path="", body=None, ctype=None):
            calls.append((m, path, body)); return 200, b"{}"
    monkeypatch.setattr(ks, "Page", FakePage)
    return calls

def test_sync_replaces_last_nights_set_with_a_fresh_one(monkeypatch):
    photos = [ph(f"P{i}") for i in range(10)]
    old = {ks.photo_id("P0"), ks.photo_id("P1"), "f" * 32}
    monkeypatch.setattr(ks, "NIGHTLY", 3)
    calls = _fake(monkeypatch, photos, old)
    assert ks.sync(check=True) == 0 and calls == []
    assert ks.sync(check=False) == 0
    puts = [c[1][1:] for c in calls if c[0] == "PUT"]
    dels = {c[1][1:] for c in calls if c[0] == "DELETE"}
    done = json.loads(calls[-1][2])
    assert len(puts) == 3 and not (set(puts) & old)
    assert dels == old and set(done["ids"]) == set(puts) and calls[-1][1] == "/done"

def test_small_library_keeps_whats_up_and_failed_previews_are_replaced(monkeypatch):
    photos = [ph("A"), ph("B"), ph("C")]
    have = {ks.photo_id("A")}
    monkeypatch.setattr(ks, "NIGHTLY", 3)
    bad = lambda p: (None, "iCloud only") if p.uuid == "B" else (b"\xff\xd8jpeg", None)
    calls = _fake(monkeypatch, photos, have, bad)
    assert ks.sync(check=False) == 0
    done = json.loads(calls[-1][2])
    assert set(done["ids"]) == {ks.photo_id("A"), ks.photo_id("C")} and done["skipped"] == 1
    assert not [c for c in calls if c[0] == "DELETE"]

def test_nothing_sendable_leaves_last_nights_photos_up(monkeypatch):
    calls = _fake(monkeypatch, [ph("A"), ph("B")], {"f" * 32}, lambda p: (None, "iCloud only"))
    assert ks.sync(check=False) == 1
    assert [c for c in calls if c[0] in ("DELETE", "POST")] == []

def test_setup_rejects_bad_codes(monkeypatch, tmp_path):
    monkeypatch.setattr(ks, "CONFIG", tmp_path / "k.json")
    for code in ["nope", "kitchen-sync:!!!", "kitchen-sync:" + __import__("base64").b64encode(b'{"host":"evil/x","client_id":"a","client_secret":"bbbbbbbbbbbbbbbbbb"}').decode()]:
        monkeypatch.setattr(ks.getpass, "getpass", lambda *_: code)
        assert ks.setup() == 1
    assert not (tmp_path / "k.json").exists()
