"""Copy the "Kitchen" album to the family page's kitchen screen, once a night.

This is NOT part of the photos connector. The connector itself still makes no
network connections; this separate script reuses its read-only library code
(photos_mcp.library) and is the only thing here that talks to the internet,
and only to the family page's /sync/kitchen route.

  kitchen_sync.py --setup   save the setup code from the family page (once)
  kitchen_sync.py --check   say what a sync would do, change nothing
  kitchen_sync.py           sync now (launchd runs this each night)

What it sends: a JPEG preview (longest side 2048 px) of each photo in the
album, named by a one-way hash of the photo's id, never its file name. The
family page strips GPS, camera, dates and captions from every photo on arrival.
Videos, hidden photos and photos only in iCloud are skipped and counted.
"""
from __future__ import annotations

import base64
import getpass
import hashlib
import json
import pathlib
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
        album = d.get("album") or "Kitchen"
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
    CONFIG.write_text(json.dumps({"host": host, "client_id": cid, "album": album}, indent=2) + "\n")
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
    return c["host"], c["client_id"], r.stdout.strip(), c.get("album") or "Kitchen"


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


# ---------- the album ----------

def album_photos(name):
    from photos_mcp import library as lib
    d = lib.db()
    albums = [a for a in d.album_info if (a.title or "") == name]
    if not albums:
        raise SystemExit(f'No album called "{name}" in Photos. Make it, add photos, and run again.')
    seen, out, skipped = set(), [], 0
    for a in albums:
        for p in a.photos:
            if p.uuid in seen:
                continue
            seen.add(p.uuid)
            if p.hidden or p.intrash or p.ismovie:
                skipped += 1
                continue
            out.append(p)
    out.sort(key=lambda p: p.date, reverse=True)
    if len(out) > MAX_PHOTOS:
        skipped += len(out) - MAX_PHOTOS
        out = out[:MAX_PHOTOS]
    return out, skipped


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
    host, cid, secret, album = load_config()
    photos, skipped = album_photos(album)
    want = {photo_id(p.uuid): p for p in photos}
    page = Page(host, cid, secret)
    have = page.ids()
    new = [i for i in want if i not in have]
    gone = [i for i in have if i not in want]
    log(f'"{album}": {len(want)} photos, {len(new)} to send, {len(gone)} to take down, {skipped} skipped')
    if check:
        log("Check only: nothing sent.")
        return 0
    sent = set()
    try:
        for i in new:
            data, why = preview_bytes(want[i])
            if data is None:
                skipped += 1
                log("skipped one photo:", why)
                continue
            s, b = page.call("PUT", "/" + i, data, "image/jpeg")
            if s == 200:
                sent.add(i)
            else:
                skipped += 1
                log(f"the page refused one photo (HTTP {s})")
    finally:
        clean_work_folder()
    for i in gone:
        page.call("DELETE", "/" + i)
    keep = [i for i in want if i in have or i in sent]
    s, b = page.call("POST", "/done", json.dumps({"ids": keep, "skipped": skipped}).encode(), "application/json")
    if s != 200:
        log(f"Couldn't save the photo list (HTTP {s}).")
        return 1
    log(f"Done: {len(sent)} sent, {len(gone)} taken down, {len(keep)} on the kitchen screen.")
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
