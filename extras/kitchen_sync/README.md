# Kitchen album copy (optional extra)

Copies the Photos album named **Kitchen** to the family page's kitchen screen
each night at 3:07 AM. Not part of the connector: the connector still makes no
network connections. This script is the only piece that does, and only to the
family page's photo entrance, with its own key.

- Sends a JPEG preview (2048 px) of each photo, named by a one-way hash, never
  the file name. The family page strips location, camera, dates and captions
  on arrival.
- Skips videos, hidden photos, Recently Deleted and photos only in iCloud,
  and keeps at most 500 (newest first). Shared albums aren't read.
- Removing a photo from the album takes it off the kitchen screen next night.
- The key lives in your Keychain ("family-base-kitchen-sync"), never in a file.

Setup, once, on the Mac Studio (after `git pull && ./install.sh`):

1. On the family page's kitchen preview, tap **Show the Mac setup command**
   and follow it (`kitchen_sync.py --setup`).
2. `~/.photos-mcp/venv/bin/python ~/photos-mcp/extras/kitchen_sync/kitchen_sync.py --check`
3. `~/photos-mcp/extras/kitchen_sync/install.sh`

The Mac needs to be on and you logged in at 3:07 AM (the Keychain is locked at
the login screen). Log: `~/Library/Logs/kitchen-sync.log`. Stop it:
`install.sh --remove`.
