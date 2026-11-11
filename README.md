# ente-lightroom-sync

Publish photos from Lightroom Classic to Ente albums through a **temporary**
staging folder that is emptied after each upload.

```
Lightroom "Ente" publish service ──▶ ~/EnteStaging/<Album>/*.jpg ──ente-sync──▶ Ente album <Album>
                                                          (folder emptied afterwards)
```

The Ente CLI cannot upload, so the Ente desktop app's *Watch folders* feature
does the upload. A plain watched folder can't simply be emptied: deleting a
file from it makes Ente move the uploaded photo to the trash. `ente-sync` gets
around that by adding the watch only for the duration of the upload:

1. quits Ente and adds a temporary watch for `~/EnteStaging`, with one album per sub-folder;
2. starts Ente and waits until each staged file is reported as synced
   (uploaded, or already in that album → skipped, no duplicate);
3. quits Ente and removes the watch entry;
4. deletes **only** the files Ente confirmed, then removes empty folders.

Files that failed or that Ente rejected stay in the folder for the next run.
If the app's state can't be read or written, nothing is deleted.

## Install

Requires macOS and Python 3.9+ (no dependencies).

```sh
git clone https://github.com/yannikmesserli/ente-lightroom-sync.git
mkdir -p ~/.local/bin ~/EnteStaging
ln -s "$PWD/ente-lightroom-sync/ente_sync.py" ~/.local/bin/ente-sync
# make sure ~/.local/bin is on your PATH (fish: fish_add_path ~/.local/bin)
```

The Ente desktop app must be installed and logged in.

## Lightroom Classic setup (one time)

1. **Publish Services** panel (left side, Library module) → **Hard Drive → Set Up…**
2. Description: `Ente`.
3. **Export Location**: *Specific folder* → `~/EnteStaging`; leave *Put in Subfolder* unchecked.
4. **File Settings**: JPEG, quality ~90, sRGB (or HEIF if you prefer).
5. **Metadata**: *All Metadata* (keeps capture date, GPS and keywords for Ente).
   Leave *Remove Location Info* unchecked if you want Ente's map/places.
6. Save.
7. Right-click the new *Ente* service → **Create Published Collection…**, named
   exactly as the Ente album you want (an existing album with that name is reused).
   Smart published collections work too.

Each published collection is written to `~/EnteStaging/<collection name>/`, and
the folder name becomes the Ente album name. With nested *published collection
sets*, only the innermost folder name counts.

## Daily use

1. In Lightroom, select the published collection(s) → **Publish**.
2. Run:
   ```fish
   ente-sync
   ```
   Ente opens, uploads, and closes again (it reopens if it was already open).
   `~/EnteStaging` ends up empty.

Lightroom keeps track of what's been published in its catalog, not on disk, so
the empty staging folder doesn't bother it. Photos you edit later show up under
*Modified Photos to Re-Publish* as usual.

Options:

| flag | meaning |
|---|---|
| `--staging PATH` | another staging folder |
| `--idle-timeout N` | stop if nothing finishes for N seconds (default 900; raise it for big videos) |
| `--keep-files` | upload but delete nothing |
| `--no-reopen` | leave Ente closed at the end |

Exit codes: `0` all done · `3` some files left (failed/unsupported) · `1` error,
nothing deleted · `2` cleanup failed, nothing deleted (see message).

## Limitations

- **Upload only.** Re-publishing an edited photo uploads a *new* file (the old
  version stays in Ente). Removing a photo from a Lightroom collection doesn't
  remove it from Ente. Clean those up in Ente by hand.
- Relies on the Ente app's internal `watch-status.json` (tested with Ente
  desktop 1.7.31). If a future version changes the format, the script stops
  without deleting anything.
- Never manually add a watch for `~/EnteStaging` in the Ente app, and don't
  empty the folder by hand while Ente is watching it.

## Disclaimer

This is an unofficial tool, not affiliated with or endorsed by Ente or Adobe.
It edits the Ente desktop app's local state file; use at your own risk and keep
your originals in Lightroom.

## License

MIT — see [LICENSE](LICENSE).
