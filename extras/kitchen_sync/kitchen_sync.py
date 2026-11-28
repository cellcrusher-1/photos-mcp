"""Put a fresh random set of library photos on the family page's kitchen screen, once a night.

This is NOT part of the photos connector. The connector itself still makes no
network connections; this separate script reuses its read-only library code
(photos_mcp.library) and is the only thing here that talks to the internet,
and only to the family page's /sync/kitchen route.

  kitchen_sync.py --setup   save the setup code from the family page (once)
  kitchen_sync.py --check   say what a sync would do, change nothing
  kitchen_sync.py           sync now (launchd runs this each night)

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


# ---------- sync ----------

def sync(check: bool) -> int:
    host, cid, secret, skip_albums = load_config()
    pool, why = library_pool(skip_albums)
    page = Page(host, cid, secret)
    have = page.ids()
    n = min(NIGHTLY, MAX_PHOTOS, len(pool))
    left_off = ", ".join(f"{v} {k}" for k, v in sorted(why.items(), key=lambda kv: -kv[1])) or "none"
    log(f"Library: {len(pool)} photos may go up; left off: {left_off}")
    log(f"Tonight: {n} at random, replacing the {len(have)} on the kitchen screen now")
    if check:
        log("Check only: nothing sent.")
        return 0
    keep, skipped = [], 0
    try:
        for p in pick(pool, have):
            if len(keep) >= n or skipped >= 3 * n:
                break
            i = photo_id(p.uuid)
            if i in have:          # pool ran short of new photos: keep this one up
                keep.append(i)
                continue
            data, reason = preview_bytes(p)
            if data is None:
                skipped += 1
                continue           # try the next one in the random order
            s, b = page.call("PUT", "/" + i, data, "image/jpeg")
            if s == 200:
                keep.append(i)
            else:
                skipped += 1
                log(f"the page refused one photo (HTTP {s})")
    finally:
        clean_work_folder()
    if not keep:
        log("No photo could be sent tonight, so last night's stay up.")
        return 1
    gone = [i for i in have if i not in set(keep)]
    for i in gone:
        page.call("DELETE", "/" + i)
    s, b = page.call("POST", "/done", json.dumps({"ids": keep, "skipped": skipped}).encode(), "application/json")
    if s != 200:
        log(f"Couldn't save the photo list (HTTP {s}).")
        return 1
    log(f"Done: {len(keep)} on the kitchen screen, {len(gone)} taken down, {skipped} couldn't be sent.")
    return 0


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if argv[:1] == ["--setup"]:
        return setup()
    if argv and argv[0] not in ("--check",):
        print(__doc__)
        return 2
    return sync(check=argv[:1] == ["--check"])


if __name__ == "__main__":
    sys.exit(main())
