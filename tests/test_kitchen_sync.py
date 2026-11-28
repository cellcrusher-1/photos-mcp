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
    monkeypatch.setattr(ks, "caption_key", lambda: None)
    monkeypatch.setattr(ks, "CAPTIONS_FILE", pathlib.Path("/nonexistent/kitchen-captions.json"))
    calls, posts = [], []
    class FakePage:
        def __init__(self, *a): pass
        def ids(self): return set(have)
        def call(self, m, path="", body=None, ctype=None):
            calls.append((m, path, body)); return 200, b"{}"
        def post_json(self, path, obj):
            posts.append((path, obj)); return 200
        def status(self, job, ok, note):
            return self.post_json("/status", {"job": job, "ok": ok, "note": note})
    monkeypatch.setattr(ks, "Page", FakePage)
    _fake.posts = posts
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

def test_nothing_sendable_is_reported_for_the_morning_brief(monkeypatch):
    _fake(monkeypatch, [ph("A")], {"f" * 32}, lambda p: (None, "iCloud only"))
    ks.sync(check=False)
    (path, body), = _fake.posts
    assert path == "/status" and body["job"] == "photos" and body["ok"] is False

def test_without_a_key_town_and_year_still_go_up_and_nothing_is_billed(monkeypatch):
    calls = _fake(monkeypatch, [ph("A", place=NS(names=NS(city=["Leavenworth"]), name="x"))], set())
    monkeypatch.setattr(ks, "Anthropic", lambda key: (_ for _ in ()).throw(AssertionError("no API call")))
    assert ks.sync(check=False) == 0
    (path, body), = _fake.posts
    assert path == "/captions" and body == {"captions": {ks.photo_id("A"): {"p": "Leavenworth · 2026"}}}

def test_caption_trouble_never_undoes_the_photo_copy(monkeypatch):
    calls = _fake(monkeypatch, [ph("A")], set())
    def boom(*a, **k): raise RuntimeError("Anthropic refused the batch (HTTP 529): overloaded")
    monkeypatch.setattr(ks, "run_captions", boom)
    assert ks.sync(check=False) == 0
    assert calls[-1][1] == "/done" and not [c for c in calls if c[0] == "DELETE"]
    assert _fake.posts == [("/status", {"job": "captions", "ok": False,
                                        "note": "Anthropic refused the batch (HTTP 529): overloaded"})]

def test_place_line_is_town_and_year_only():
    d = dt.datetime(2019, 7, 4)
    assert ks.place_line(NS(date=d, place=NS(names=NS(city=["Leavenworth"]), name="1 Main St, Leavenworth"))) == "Leavenworth · 2019"
    assert ks.place_line(NS(date=d, place=NS(names=NS(city=[]), name="Lake Chelan, WA, USA"))) == "Lake Chelan · 2019"
    assert ks.place_line(NS(date=d, place=None)) == "2019"
    assert ks.place_line(NS(date=None, place=None)) == ""
    assert ks.place_line(NS(date=d, place=NS(names=NS(city=["<b>Town</b>"]), name=""))) == "bTownb · 2019"

def test_clean_caption_is_one_short_safe_line():
    assert ks.clean_caption("Dog asleep on a sunny porch\nSecond line") == "Dog asleep on a sunny porch"
    assert ks.clean_caption('"Kids splashing at the lake."') == "Kids splashing at the lake"
    assert ks.clean_caption("SKIP") == "" and ks.clean_caption("  ") == "" and ks.clean_caption(None) == ""
    assert ks.clean_caption("<script>alert(1)</script> Snowy pines 🌲") == "scriptalert1script Snowy pines"
    long = ks.clean_caption("word " * 30)
    assert len(long) <= ks.CAPTION_MAX and not long.endswith(" ")

def test_cost_counts_cache_tokens_at_half_price():
    c = ks.cost({"input_tokens": 1_000_000, "cache_read_input_tokens": 0, "output_tokens": 1_000_000})
    assert abs(c - (0.10 + 0.50) / 2) < 1e-9
    assert ks.cost(None) == 0

def test_caption_request_sends_only_the_image():
    r = ks.caption_request("a" * 32, b"\xff\xd8jpeg")
    assert r["custom_id"] == "a" * 32
    pr = r["params"]
    assert pr["model"] == "claude-haiku-5-5" and "temperature" not in pr
    content = pr["messages"][0]["content"]
    assert [b["type"] for b in content] == ["image", "text"] and content[1]["text"] == "Caption this photo."

class FakeAPI:
    def __init__(self, ends_after=1, text="Dog asleep on the porch"):
        self.ends_after, self.text, self.made, self.deleted, self.polls = ends_after, text, [], [], 0
    def create(self, reqs):
        self.made.append([r["custom_id"] for r in reqs]); return f"msgbatch_{len(self.made)}"
    def get(self, bid):
        self.polls += 1
        return {"processing_status": "ended" if self.polls >= self.ends_after else "in_progress"}
    def results(self, bid):
        for pid in self.made[-1]:
            yield {"custom_id": pid, "result": {"type": "succeeded", "message": {
                "content": [{"type": "thinking", "thinking": "x"}, {"type": "text", "text": self.text}],
                "usage": {"input_tokens": 1100, "output_tokens": 60}}}}
    def delete(self, bid):
        self.deleted.append(bid); return 200

class CapPage:
    def __init__(self): self.posts = []
    def post_json(self, path, obj): self.posts.append((path, obj)); return 200

def _captions(monkeypatch, tmp_path, api, hour=3):
    monkeypatch.setattr(ks, "CAPTIONS_FILE", tmp_path / "caps.json")
    monkeypatch.setattr(ks, "Anthropic", lambda key: api)
    monkeypatch.setattr(ks, "small_jpeg", lambda p: b"\xff\xd8jpeg")
    monkeypatch.setattr(ks, "clean_work_folder", lambda: None)
    t0 = dt.datetime(2026, 11, 29, hour, 10).timestamp()
    clock = {"t": t0}
    sleep = lambda s: clock.__setitem__("t", clock["t"] + s)
    return dict(sleep=sleep, now=lambda: clock["t"], key="sk-ant-test")

def test_captions_are_made_once_cached_and_the_batch_deleted(monkeypatch, tmp_path):
    api = FakeAPI(ends_after=3)
    kw = _captions(monkeypatch, tmp_path, api)
    tonight = {ks.photo_id(u): ph(u) for u in "ABC"}
    page = CapPage()
    ok, note = ks.run_captions(page, tonight, 2.0, **kw)
    assert ok and note.startswith("3 captioned, $0.00") and api.deleted == ["msgbatch_1"]
    (path, body), = page.posts
    assert path == "/captions" and all(v["d"] == "Dog asleep on the porch" for v in body["captions"].values())
    st = json.loads((tmp_path / "caps.json").read_text())
    assert st["pending"] is None and st["spent"] > 0 and len(st["captions"]) == 3
    # Next night the same photos cost nothing.
    ok, note = ks.run_captions(CapPage(), tonight, 2.0, **kw)
    assert len(api.made) == 1 and "nothing new to caption" in note

def test_a_late_batch_waits_for_tomorrow_and_isnt_sent_twice(monkeypatch, tmp_path):
    api = FakeAPI(ends_after=10_000)
    kw = _captions(monkeypatch, tmp_path, api, hour=4)
    tonight = {ks.photo_id("A"): ph("A")}
    ok, note = ks.run_captions(CapPage(), tonight, 2.0, **kw)
    assert ok and "batch still running" in note and api.deleted == []
    assert json.loads((tmp_path / "caps.json").read_text())["pending"]["batch"] == "msgbatch_1"
    api.ends_after = 0
    ok, note = ks.run_captions(CapPage(), tonight, 2.0, **kw)
    assert len(api.made) == 1 and api.deleted == ["msgbatch_1"] and "1 from last night's batch" in note

def test_the_monthly_cap_stops_new_batches(monkeypatch, tmp_path):
    api = FakeAPI()
    kw = _captions(monkeypatch, tmp_path, api)
    (tmp_path / "caps.json").write_text(json.dumps({"month": "2026-11", "spent": 2.0, "captions": {}, "pending": None}))
    tonight = {ks.photo_id(u): ph(u) for u in "ABCDEF"}
    page = CapPage()
    ok, note = ks.run_captions(page, tonight, 2.0, **kw)
    assert api.made == [] and "monthly cap $2.00 reached" in note
    assert set(page.posts[0][1]["captions"]) == set(tonight)   # town and year still go up
    # A new month starts again from $0.
    (tmp_path / "caps.json").write_text(json.dumps({"month": "2026-10", "spent": 2.0, "captions": {}, "pending": None}))
    ks.run_captions(CapPage(), tonight, 2.0, **kw)
    assert len(api.made[0]) == 6

def test_a_skip_answer_leaves_only_town_and_year(monkeypatch, tmp_path):
    api = FakeAPI(text="SKIP")
    kw = _captions(monkeypatch, tmp_path, api)
    page = CapPage()
    ks.run_captions(page, {ks.photo_id("A"): ph("A")}, 2.0, **kw)
    assert page.posts[0][1]["captions"][ks.photo_id("A")] == {"p": "2026"}

def test_no_key_means_no_captions_run(monkeypatch):
    monkeypatch.setattr(ks, "caption_key", lambda: None)
    assert ks.run_captions(CapPage(), {}, 2.0) is None

def test_cap_setting_is_clamped(monkeypatch, tmp_path):
    monkeypatch.setattr(ks, "CONFIG", tmp_path / "k.json")
    assert ks.cap_usd() == ks.DEFAULT_CAP
    for v, want in [(5, 5.0), (500, 20.0), (-3, 0.0), ("x", ks.DEFAULT_CAP)]:
        (tmp_path / "k.json").write_text(json.dumps({"caption_cap_usd": v}))
        assert ks.cap_usd() == want

def test_setup_captions_rejects_things_that_arent_keys(monkeypatch):
    ran = []
    monkeypatch.setattr(ks.subprocess, "run", lambda *a, **k: ran.append(a))
    for bad in ["", "hello", "sk-ant-short", "sk-proj-" + "a" * 40]:
        monkeypatch.setattr(ks.getpass, "getpass", lambda *_: bad)
        assert ks.setup_captions() == 1
    assert ran == []
