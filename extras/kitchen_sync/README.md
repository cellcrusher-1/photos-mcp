# Kitchen screen photos (optional extra)

Each night at 3:07 AM this puts a fresh random set of 300 photos from the
whole Photos library on the family page's kitchen screen, and takes down last
night's set. Not part of the connector: the connector still makes no network
connections. This script is the only piece that does, and only to the family
page's photo entrance, with its own key.

What never goes up:

- Videos, screenshots, hidden photos and Recently Deleted.
- Photos in shared albums, "Shared with You" and shared moments (other people's photos).
- Photos that Photos tags as documents, receipts, text, paper, cards, screens
  and the like (`SKIP_LABELS`), PNG and GIF images, images under 800 px on a
  side, and the extra shots of a burst.
- Any album you name: add its exact name to `"skip_albums"` in
  `~/.photos-mcp/kitchen-sync.json`, for example `"skip_albums": ["Work", "Receipts"]`.

How it sends:

- A JPEG preview (2048 px) of each photo, named by a one-way hash, never the
  file name. The family page strips location, camera, dates and captions on
  arrival.
- Photos that weren't up last night are picked first. If a preview can't be
  made (for example, a photo only in iCloud), it tries the next one.
- 300 in and 300 out a night stays inside Cloudflare KV's free daily limits.
- The key lives in your Keychain ("family-base-kitchen-sync"), never in a file.

Setup, once, on the Mac Studio (after `git pull`):

1. On the family page's kitchen preview, tap **Show the Mac setup command**
   and follow it (`kitchen_sync.py --setup`).
2. `~/.photos-mcp/venv/bin/python ~/photos-mcp/extras/kitchen_sync/kitchen_sync.py --check`
   shows how many photos may go up and how many each filter leaves off.
3. `~/photos-mcp/extras/kitchen_sync/install.sh`

The Mac needs to be on and you logged in at 3:07 AM (the Keychain is locked at
the login screen). Log: `~/Library/Logs/kitchen-sync.log`. Stop it:
`install.sh --remove`.
