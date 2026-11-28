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

## Captions (optional, about $0.03 a night)

When it's set up, each photo on the kitchen screen gets two lines in the
corner: a short description made by Claude, and the town and year.

- **Town and year** come from the photo's own Photos details, on the Mac, at
  town level only. They're free and show even without the captions key.
- **The description** comes from Claude Haiku 5.5 through Anthropic's Message
  Batches API, which charges half price for jobs that can wait. Only photos that
  have never had a caption are sent, as a 1024 px copy of the image and nothing
  else: no file name, place, date, GPS or people's names. The prompt asks for at
  most 8 words, with no names and no guesses about who anyone is.
- Captions are kept in `~/.photos-mcp/kitchen-captions.json`, so a photo that
  comes round again costs nothing. Each batch is deleted from Anthropic once its
  captions are in.
- **Cost:** about $0.026 a night for 300 new photos, so under $1 a month, and
  less as more of the library has captions. Spending is counted from what the
  API reports and stops for the month at `"caption_cap_usd"` in
  `~/.photos-mcp/kitchen-sync.json` (default `2`, at most `20`).
- A batch that isn't back by 4:20 AM is collected the next night. Captions
  never undo the photo copy: if Anthropic is down, the photos still change and
  the morning brief says captions failed.

Set it up (after the photo setup above):

1. In the Claude Console, make a separate API key just for this, with a monthly
   spend limit.
2. `~/.photos-mcp/venv/bin/python ~/photos-mcp/extras/kitchen_sync/kitchen_sync.py --setup-captions`
   and paste the key. It goes in your Keychain ("family-base-kitchen-captions").
3. `--check` now says "Captions: set up".

Turn it off, fastest first:

- `kitchen_sync.py --stop-captions` takes the key out of the Keychain. Photos
  keep changing each night, with town and year only.
- Delete (or set a $0 limit on) that API key in the Claude Console. Nothing can
  be billed after that, even if the key is still on the Mac.
- `install.sh --remove` stops the whole nightly job.

The morning brief: each night the script tells the family page whether the
photo copy and the captions ran ("photos" and "captions" job lines), so the
brief can say if either had trouble.
