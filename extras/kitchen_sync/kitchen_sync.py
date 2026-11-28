"""Put a fresh random set of library photos on the family page's kitchen screen, once a night.

This is NOT part of the photos connector. The connector itself still makes no
network connections; this separate script reuses its read-only library code
(photos_mcp.library) and is the only thing here that talks to the internet,
and only to the family page's /sync/kitchen route.

  kitchen_sync.py --setup            save the setup code from the family page (once)
  kitchen_sync.py --setup-captions   save the Anthropic API key for captions (once)
  kitchen_sync.py --stop-captions    take that key out of the Keychain (captions stop)
  kitchen_sync.py --check            say what a sync would do, change nothing
  kitchen_sync.py                    sync now (launchd runs this each night)

Which photos: Christopher chose "Whole library" (Nov 28, 2026), so each night
it picks NIGHTLY photos at random from the whole library, preferring ones that
weren't up the night before. It never picks videos, screenshots, hidden or
deleted photos, photos in shared albums or "Shared with You", photos Photos
tags as documents, receipts, text or the like, PNG or GIF images, small images
(saved from the web or messages), or the extra shots of a burst. Leave a whole
album out by adding its exact name to "skip_albums" in ~/.photos-mcp/kitchen-sync.json,
for example "skip_albums": ["Work", "Receipts"].

What it sends: a JPEG preview (longest side 2048 px) of each photo, named by
a one-way hash of the photo's id, never its file name. The family page strips
GPS, camera, dates and captions from every photo on arrival.

Captions (Christopher's pick, Nov 28, 2026: "Pick 1", a short description plus
town and year). Town and year come from the photo's own Photos details, on this
Mac, and are sent to the family page as text. The short description comes from
Claude (Haiku 5.5) through Anthropic's Message Batches API at half price: only
photos that have never had one, a 1024 px copy of the image and nothing else
(no file name, place, date, GPS or people's names). Captions are kept in
~/.photos-mcp/kitchen-captions.json, so a photo that comes round again costs
nothing. Spending is counted from what the API reports and stops for the month
at "caption_cap_usd" in kitchen-sync.json (default $2). A batch not back by
4:20 AM is collected the next night. Each batch is deleted from Anthropic once
its captions are in. Without the API key in the Keychain, none of this runs.
"""
from __future__ import annotations

import base64
import getpass
import hashlib
import json
import pathlib
import random
import re
import shutil
import ssl
import subprocess
import sys
import time
import urllib.error
import urllib.request

CONFIG = pathlib.Path.home() / ".photos-mcp" / "kitchen-sync.json"
KEYCHAIN_SERVICE = "family-base-kitchen-sync"
KEYCHAIN_ACCOUNT = "kitchen"
WORK_FOLDER = "Kitchen sync"   # under ~/Pictures/Claude Exports, emptied after each run
MAX_PHOTOS = 500               # the family page keeps at most this many
NIGHTLY = 300                  # a fresh set this big each night; 300 in and 300 out
                               # stays inside Cloudflare KV's free daily limits (1,000 each)
MIN_SIDE = 800                 # smaller images are usually saved from the web or a message
# Photos' own image tags that mean "paperwork, not a memory". Matched lower case.
SKIP_LABELS = {
    "document", "documents", "receipt", "text", "paper", "handwriting", "whiteboard",
    "blackboard", "diagram", "chart", "menu", "invoice", "letter", "form", "credit card",
    "id card", "identity card", "passport", "business card", "barcode", "qr code",
    "screenshot", "x-ray", "x ray", "medical", "prescription", "computer screen",
    "monitor", "display", "screen",
}
SKIP_UTIS = {"public.png", "com.compuserve.gif"}
MAX_BYTES = 4 * 1024 * 1024    # and refuses anything bigger
SIZES = (2048, 1600, 1200)     # try smaller if a preview is too big
# ---- captions ----
CAPTIONS_FILE = pathlib.Path.home() / ".photos-mcp" / "kitchen-captions.json"
CAPTION_KEY_SERVICE = "family-base-kitchen-captions"
CAPTION_KEY_ACCOUNT = "anthropic"
CAPTION_MODEL = "claude-haiku-5-5"
CAPTION_PX = 1024                       # about 1,050 image tokens a photo
API = "https://api.anthropic.com/v1/messages/batches"
API_VERSION = "2026-10-21"
PRICE_IN, PRICE_OUT = 0.10, 0.50        # $ per million tokens, Haiku 5.5 (prompts under 100K)
BATCH_SHARE = 0.5                       # the Batches API charges half
EST_PER_PHOTO = 0.0001                  # $ a photo, a cautious guess used before a batch is sent
DEFAULT_CAP = 2.00                      # $ a calendar month
DEADLINE = (4, 20)                      # stop waiting for the batch at 4:20 AM, Mac time
POLL_S = 60
CAPTION_MAX = 60
CAPTION_SYSTEM = (
    "You write one short caption for a family photo that shows on a screen in the family's kitchen. "
    "Describe what is happening in plain, warm words, at most 8 words, like \"Sledding on the big hill\" "
    "or \"Birthday cake with candles\". Never name or guess who anyone is, their age or their relationship. "
    "No place names, dates, quotes, hashtags or emoji. "
    "If the photo is mostly text, a document, a screen or a receipt, reply with only the word SKIP. "
    "Reply with the caption only."
)
HOST_RE = re.compile(r"[a-z0-9.-]+\.[a-z]{2,}")
ID_RE = re.compile(r"[a-f0-9]{32}")


def log(*a):
    print(time.strftime("%Y-%m-%d %H:%M:%S"), *a, flush=True)


def photo_id(uuid: str) -> str:
    """The name the family page knows a photo by: a hash, so the real id never leaves the Mac."""
    return hashlib.sha256(("kitchen:" + uuid.upper()).encode()).hexdigest()[:32]


# ---------- setup ----------

def setup() -> int:
    raw = getpass.getpass("Setup code (it won't show as you paste): ").strip()
    if not raw.startswith("kitchen-sync:"):
        print("That isn't a setup code. Copy it again from the kitchen preview page.")
        return 1
    try:
        d = json.loads(base64.b64decode(raw.split(":", 1)[1]))
        host, cid, secret = d["host"], d["client_id"], d["client_secret"]
    except Exception:
        print("That setup code is damaged. Copy it again, all of it.")
        return 1
    if not HOST_RE.fullmatch(host) or not isinstance(cid, str) or not isinstance(secret, str) or len(secret) < 16:
        print("That setup code doesn't look right. Nothing was saved.")
        return 1
    r = subprocess.run(["/usr/bin/security", "add-generic-password", "-U",
                        "-a", KEYCHAIN_ACCOUNT, "-s", KEYCHAIN_SERVICE, "-w", secret],
                       capture_output=True, text=True)
    if r.returncode != 0:
        print("Couldn't save to the Keychain:", r.stderr.strip()[:200])
        return 1
    CONFIG.parent.mkdir(parents=True, exist_ok=True)
    old = {}
    if CONFIG.exists():
        try:
            old = json.loads(CONFIG.read_text())
        except Exception:
            old = {}
    CONFIG.write_text(json.dumps({"host": host, "client_id": cid,
                                  "skip_albums": old.get("skip_albums", [])}, indent=2) + "\n")
    CONFIG.chmod(0o600)
    print(f"Saved. The key is in your Keychain as \"{KEYCHAIN_SERVICE}\".")
    print("Next: run the same line with --check instead of --setup.")
    return 0


def load_config():
    if not CONFIG.exists():
        raise SystemExit("Not set up yet: run with --setup first.")
    c = json.loads(CONFIG.read_text())
    if not HOST_RE.fullmatch(c.get("host", "")):
        raise SystemExit(f"{CONFIG} has a bad host.")
    r = subprocess.run(["/usr/bin/security", "find-generic-password",
                        "-a", KEYCHAIN_ACCOUNT, "-s", KEYCHAIN_SERVICE, "-w"],
                       capture_output=True, text=True)
    if r.returncode != 0:
        raise SystemExit("The key isn't in the Keychain (or the Keychain is locked). Run --setup again.")
    skip = [a for a in c.get("skip_albums", []) if isinstance(a, str) and a.strip()]
    return c["host"], c["client_id"], r.stdout.strip(), skip


# ---------- talking to the family page ----------

class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):
        return None  # a redirect means Access wants a sign-in: the key is wrong or expired


def ssl_context():
    try:
        import certifi  # comes with osxphotos' dependencies
        return ssl.create_default_context(cafile=certifi.where())
    except Exception:
        return ssl.create_default_context()


class Page:
    def __init__(self, host, cid, secret):
        self.base = f"https://{host}/sync/kitchen"
        self.headers = {"CF-Access-Client-Id": cid, "CF-Access-Client-Secret": secret,
                        "User-Agent": "kitchen-sync"}
        self.opener = urllib.request.build_opener(NoRedirect, urllib.request.HTTPSHandler(context=ssl_context()))

    def call(self, method, path="", body=None, ctype=None):
        h = dict(self.headers)
        if ctype:
            h["Content-Type"] = ctype
        req = urllib.request.Request(self.base + path, data=body, method=method, headers=h)
        try:
            with self.opener.open(req, timeout=60) as r:
                return r.status, r.read()
        except urllib.error.HTTPError as e:
            if e.code in (301, 302, 303, 307, 308, 401, 403):
                raise SystemExit(f"The family page refused the key (HTTP {e.code}). Ask Claude for a new one.")
            return e.code, e.read()

    def post_json(self, path, obj):
        try:
            return self.call("POST", path, json.dumps(obj).encode(), "application/json")[0]
        except SystemExit:
            return 0

    def status(self, job, ok, note):
        """One line for the morning brief: did this job run, and what went wrong."""
        return self.post_json("/status", {"job": job, "ok": bool(ok), "note": str(note)[:120]})

    def ids(self):
        s, b = self.call("GET")
        if s != 200:
            raise SystemExit(f"Couldn't read the photo list (HTTP {s}).")
        return {i for i in json.loads(b).get("ids", []) if ID_RE.fullmatch(i)}


# ---------- which photos may go up ----------

def why_not(p, skip_uuids=frozenset()):
    """The reason a photo stays off the kitchen wall, or None if it may go up."""
    if p.intrash or p.hidden:
        return "hidden or deleted"
    if p.ismovie or not getattr(p, "isphoto", True):
        return "video"
    if getattr(p, "screenshot", False) or getattr(p, "screen_recording", False):
        return "screenshot"
    if getattr(p, "shared", False) or getattr(p, "syndicated", False) or getattr(p, "shared_moment", False):
        return "shared with you"
    if p.uuid in skip_uuids:
        return "in an album you left out"
    if getattr(p, "burst", False) and not (getattr(p, "burst_key", False) or getattr(p, "burst_selected", False)):
        return "extra burst shot"
    if (getattr(p, "uti", "") or "").lower() in SKIP_UTIS:
        return "PNG or GIF"
    w, h = getattr(p, "width", 0) or 0, getattr(p, "height", 0) or 0
    if w and h and min(w, h) < MIN_SIDE:
        return "small image"
    try:
        labels = {str(x).lower() for x in (p.labels or [])}
    except Exception:          # Photos hasn't finished analysing this library yet
        labels = set()
    if labels & SKIP_LABELS:
        return "document, receipt or text"
    return None


def library_pool(skip_albums=()):
    """Every photo that may go up, plus a count of the rest by reason (counts only)."""
    from photos_mcp import library as lib
    d = lib.db()
    skip_uuids = set()
    for a in d.album_info:
        if (a.title or "") in skip_albums:
            skip_uuids.update(p.uuid for p in a.photos)
    pool, why = [], {}
    for p in d.photos(intrash=False):
        r = why_not(p, skip_uuids)
        if r:
            why[r] = why.get(r, 0) + 1
        else:
            pool.append(p)
    if not pool:
        raise SystemExit("No photos in the library passed the filters, so nothing was changed.")
    return pool, why


def pick(pool, have, rng=random):
    """A random order to try: photos not up last night first, then the rest."""
    fresh = [p for p in pool if photo_id(p.uuid) not in have]
    old = [p for p in pool if photo_id(p.uuid) in have]
    rng.shuffle(fresh)
    rng.shuffle(old)
    return fresh + old


def preview_bytes(p):
    from photos_mcp import library as lib
    for px in SIZES:
        r = lib.export_preview(p, WORK_FOLDER, px)
        if not r.get("ok"):
            return None, r.get("why", "couldn't make a preview")
        path = pathlib.Path(r["path"])
        data = path.read_bytes()
        path.unlink(missing_ok=True)
        if len(data) <= MAX_BYTES:
            return data, None
    return None, "too big even at 1200 px"


def clean_work_folder():
    from photos_mcp import library as lib
    shutil.rmtree(lib.EXPORT_ROOT / WORK_FOLDER, ignore_errors=True)


# ---------- captions ----------

def place_line(p) -> str:
    """'Leavenworth · 2019' from the photo's own Photos details (town level only), or just the year."""
    town = ""
    pl = getattr(p, "place", None)
    if pl:
        names = getattr(pl, "names", None)
        city = getattr(names, "city", None) if names else None
        if isinstance(city, (list, tuple)) and city and city[0]:
            town = str(city[0])
        elif getattr(pl, "name", None):
            town = str(pl.name).split(",")[0]
    town = re.sub(r"[^\w .'&-]", "", town).strip()[:40]
    d = getattr(p, "date", None)
    year = str(d.year) if d else ""
    return " · ".join(x for x in (town, year) if x)


def clean_caption(text: str) -> str:
    """One plain line, at most CAPTION_MAX characters, cut at a word. SKIP means no caption."""
    line = (text or "").strip().splitlines()[0] if (text or "").strip() else ""
    line = re.sub(r"[^\w\s,.!?'&-]", "", line).strip().strip("'\".").strip()
    line = re.sub(r"\s+", " ", line)
    if not line or line.upper() == "SKIP":
        return ""
    if len(line) > CAPTION_MAX:
        line = line[:CAPTION_MAX].rsplit(" ", 1)[0]
    return line


def caption_key():
    try:
        r = subprocess.run(["/usr/bin/security", "find-generic-password",
                            "-a", CAPTION_KEY_ACCOUNT, "-s", CAPTION_KEY_SERVICE, "-w"],
                           capture_output=True, text=True)
    except OSError:
        return None
    return r.stdout.strip() if r.returncode == 0 and r.stdout.strip() else None


def load_state(month: str) -> dict:
    st = {}
    if CAPTIONS_FILE.exists():
        try:
            st = json.loads(CAPTIONS_FILE.read_text())
        except Exception:
            st = {}
    st.setdefault("captions", {})
    st.setdefault("pending", None)
    if st.get("month") != month:
        st["month"], st["spent"] = month, 0.0
    st.setdefault("spent", 0.0)
    return st


def save_state(st: dict):
    CAPTIONS_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = CAPTIONS_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(st, indent=1) + "\n")
    tmp.chmod(0o600)
    tmp.replace(CAPTIONS_FILE)


def cost(usage: dict) -> float:
    u = usage or {}
    tin = (u.get("input_tokens") or 0) + (u.get("cache_creation_input_tokens") or 0) + (u.get("cache_read_input_tokens") or 0)
    return (tin * PRICE_IN + (u.get("output_tokens") or 0) * PRICE_OUT) / 1e6 * BATCH_SHARE


class Anthropic:
    """Just the four Message Batches calls this needs, over plain HTTPS (no extra library)."""

    def __init__(self, key):
        self.h = {"x-api-key": key, "anthropic-version": API_VERSION, "content-type": "application/json",
                  "User-Agent": "kitchen-sync"}
        self.ctx = ssl_context()

    def _req(self, method, url, body=None, timeout=300):
        req = urllib.request.Request(url, data=body, method=method, headers=self.h)
        try:
            with urllib.request.urlopen(req, timeout=timeout, context=self.ctx) as r:
                return r.status, r.read()
        except urllib.error.HTTPError as e:
            return e.code, e.read()
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            return 0, str(e).encode()

    def create(self, requests):
        s, b = self._req("POST", API, json.dumps({"requests": requests}).encode())
        if s != 200:
            raise RuntimeError(f"Anthropic refused the batch (HTTP {s}): {api_error(b)}")
        return json.loads(b)["id"]

    def get(self, bid):
        s, b = self._req("GET", f"{API}/{bid}", timeout=60)
        if s != 200:
            raise RuntimeError(f"couldn't check the batch (HTTP {s}): {api_error(b)}")
        return json.loads(b)

    def results(self, bid):
        s, b = self._req("GET", f"{API}/{bid}/results")
        if s != 200:
            raise RuntimeError(f"couldn't read the batch results (HTTP {s}): {api_error(b)}")
        for line in b.decode().splitlines():
            if line.strip():
                yield json.loads(line)

    def delete(self, bid):
        return self._req("DELETE", f"{API}/{bid}", timeout=60)[0]


def api_error(b) -> str:
    try:
        return json.loads(b)["error"]["message"][:80]
    except Exception:
        return (b or b"")[:80].decode(errors="replace")


def caption_request(pid, jpeg: bytes) -> dict:
    return {"custom_id": pid, "params": {
        "model": CAPTION_MODEL,
        "max_tokens": 1024,
        "output_config": {"effort": "low"},
        "system": CAPTION_SYSTEM,
        "messages": [{"role": "user", "content": [
            {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg",
                                         "data": base64.standard_b64encode(jpeg).decode()}},
            {"type": "text", "text": "Caption this photo."},
        ]}],
    }}


def small_jpeg(p):
    from photos_mcp import library as lib
    r = lib.export_preview(p, WORK_FOLDER, CAPTION_PX)
    if not r.get("ok"):
        return None
    path = pathlib.Path(r["path"])
    data = path.read_bytes()
    path.unlink(missing_ok=True)
    return data


def collect(api, st) -> tuple[int, float]:
    """Read a finished batch into the cache, count its cost, delete it from Anthropic."""
    pend = st["pending"]
    got, spent = 0, 0.0
    for r in api.results(pend["batch"]):
        res = r.get("result") or {}
        pid = r.get("custom_id", "")
        if res.get("type") != "succeeded" or not ID_RE.fullmatch(pid):
            continue
        msg = res.get("message") or {}
        spent += cost(msg.get("usage"))
        text = " ".join(b.get("text", "") for b in msg.get("content", []) if b.get("type") == "text")
        st["captions"][pid] = {"d": clean_caption(text)}
        got += 1
    st["spent"] = round(st["spent"] + spent, 6)
    st["pending"] = None
    save_state(st)
    api.delete(pend["batch"])
    return got, spent


def past_deadline(now=None) -> bool:
    t = time.localtime(now)
    return (t.tm_hour, t.tm_min) >= DEADLINE and t.tm_hour < 12


def run_captions(page, tonight: dict, cap: float, *, sleep=time.sleep, now=time.time, key=None):
    """tonight: {photo id: photo}. Returns (ok, note) for the brief, or None if captions aren't set up."""
    key = key or caption_key()
    if not key:
        return None
    api = Anthropic(key)
    st = load_state(time.strftime("%Y-%m", time.localtime(now())))
    notes = []
    # 1. A batch from an earlier night that wasn't back in time.
    if st["pending"]:
        b = api.get(st["pending"]["batch"])
        if b.get("processing_status") == "ended":
            got, c = collect(api, st)
            notes.append(f"{got} from last night's batch")
    # 2. Tonight's photos that have never had a caption.
    waiting = set(st["pending"]["ids"]) if st["pending"] else set()
    need = [i for i in tonight if i not in st["captions"] and i not in waiting]
    room = max(0, int((cap - st["spent"]) / EST_PER_PHOTO))
    capped = len(need) > room
    need = need[:room]
    sent = 0
    if need and not st["pending"]:
        reqs = []
        try:
            for i in need:
                data = small_jpeg(tonight[i])
                if data:
                    reqs.append(caption_request(i, data))
        finally:
            clean_work_folder()
        if reqs:
            bid = api.create(reqs)
            st["pending"] = {"batch": bid, "ids": [r["custom_id"] for r in reqs], "at": int(now())}
            save_state(st)
            sent = len(reqs)
    # 3. Wait for it, until 4:20 AM.
    late = False
    if st["pending"]:
        while True:
            b = api.get(st["pending"]["batch"])
            if b.get("processing_status") == "ended":
                got, c = collect(api, st)
                notes.insert(0, f"{got} captioned, ${c:.2f}")
                break
            if past_deadline(now()):
                late = True
                break
            sleep(POLL_S)
    page_captions(page, tonight, st)
    if late:
        notes.append("batch still running, those captions come tomorrow night")
    if capped:
        notes.append(f"monthly cap ${cap:.2f} reached, the rest wait for next month")
    if not notes:
        notes.append("nothing new to caption" if not sent else f"{sent} sent")
    notes.append(f"${st['spent']:.2f} this month")
    return True, ", ".join(notes)


def page_captions(page, tonight: dict, st: dict):
    """Send tonight's captions (description from the cache, town and year from Photos) to the family page."""
    caps = {}
    for i, p in tonight.items():
        c = {"p": place_line(p)}
        d = (st or {}).get("captions", {}).get(i, {}).get("d")
        if d:
            c["d"] = d
        caps[i] = c
    if caps:
        page.post_json("/captions", {"captions": caps})


def setup_captions() -> int:
    raw = getpass.getpass("Anthropic API key for captions (it won't show as you paste): ").strip()
    if not re.fullmatch(r"sk-ant-[A-Za-z0-9_-]{20,}", raw):
        print("That doesn't look like an Anthropic API key (they start sk-ant-). Nothing was saved.")
        return 1
    r = subprocess.run(["/usr/bin/security", "add-generic-password", "-U",
                        "-a", CAPTION_KEY_ACCOUNT, "-s", CAPTION_KEY_SERVICE, "-w", raw],
                       capture_output=True, text=True)
    if r.returncode != 0:
        print("Couldn't save to the Keychain:", r.stderr.strip()[:200])
        return 1
    print(f"Saved in your Keychain as \"{CAPTION_KEY_SERVICE}\". Captions start with the next nightly run.")
    return 0


def stop_captions() -> int:
    subprocess.run(["/usr/bin/security", "delete-generic-password",
                    "-a", CAPTION_KEY_ACCOUNT, "-s", CAPTION_KEY_SERVICE], capture_output=True, text=True)
    print("The captions key is out of the Keychain, so no more captions are made. Photos still copy each night.")
    print("Captions already made stay on the kitchen screen. To stop the key for good, delete it in the Claude Console too.")
    return 0


# ---------- sync ----------

def sync(check: bool) -> int:
    host, cid, secret, skip_albums = load_config()
    page = Page(host, cid, secret)
    try:
        return _sync(page, check, skip_albums)
    except SystemExit as e:
        if not check:
            page.status("photos", False, str(e))
        raise


def _sync(page, check, skip_albums) -> int:
    pool, why = library_pool(skip_albums)
    have = page.ids()
    n = min(NIGHTLY, MAX_PHOTOS, len(pool))
    left_off = ", ".join(f"{v} {k}" for k, v in sorted(why.items(), key=lambda kv: -kv[1])) or "none"
    log(f"Library: {len(pool)} photos may go up; left off: {left_off}")
    log(f"Tonight: {n} at random, replacing the {len(have)} on the kitchen screen now")
    if check:
        st = load_state(time.strftime("%Y-%m"))
        log(f"Captions: {'set up' if caption_key() else 'not set up (no key)'}; "
            f"{len(st['captions'])} made so far, ${st['spent']:.2f} spent this month of ${cap_usd():.2f}")
        log("Check only: nothing sent.")
        return 0
    keep, tonight, skipped = [], {}, 0
    try:
        for p in pick(pool, have):
            if len(keep) >= n or skipped >= 3 * n:
                break
            i = photo_id(p.uuid)
            if i in have:          # pool ran short of new photos: keep this one up
                keep.append(i)
                tonight[i] = p
                continue
            data, reason = preview_bytes(p)
            if data is None:
                skipped += 1
                continue           # try the next one in the random order
            s, b = page.call("PUT", "/" + i, data, "image/jpeg")
            if s == 200:
                keep.append(i)
                tonight[i] = p
            else:
                skipped += 1
                log(f"the page refused one photo (HTTP {s})")
    finally:
        clean_work_folder()
    if not keep:
        log("No photo could be sent tonight, so last night's stay up.")
        page.status("photos", False, "no photo could be sent, last night's stay up")
        return 1
    gone = [i for i in have if i not in set(keep)]
    for i in gone:
        page.call("DELETE", "/" + i)
    s, b = page.call("POST", "/done", json.dumps({"ids": keep, "skipped": skipped}).encode(), "application/json")
    if s != 200:
        log(f"Couldn't save the photo list (HTTP {s}).")
        page.status("photos", False, f"couldn't save the photo list (HTTP {s})")
        return 1
    log(f"Done: {len(keep)} on the kitchen screen, {len(gone)} taken down, {skipped} couldn't be sent.")
    # Captions never undo the photo copy: any trouble here is logged and reported, and the photos stay.
    try:
        r = run_captions(page, tonight, cap_usd())
        if r is None:
            page_captions(page, tonight, None)   # town and year still show, free
            log("Captions: not set up, so town and year only.")
        else:
            log("Captions:", r[1])
            page.status("captions", *r)
    except Exception as e:
        msg = str(e)[:120] or e.__class__.__name__
        log("Captions failed:", msg)
        page.status("captions", False, msg)
    return 0


def cap_usd() -> float:
    try:
        v = float(json.loads(CONFIG.read_text()).get("caption_cap_usd", DEFAULT_CAP))
        return max(0.0, min(v, 20.0))
    except Exception:
        return DEFAULT_CAP


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if argv[:1] == ["--setup"]:
        return setup()
    if argv[:1] == ["--setup-captions"]:
        return setup_captions()
    if argv[:1] == ["--stop-captions"]:
        return stop_captions()
    if argv and argv[0] not in ("--check",):
        print(__doc__)
        return 2
    return sync(check=argv[:1] == ["--check"])


if __name__ == "__main__":
    sys.exit(main())
