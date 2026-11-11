#!/usr/bin/env python3
"""
ente-sync: upload a temporary staging folder to Ente through the Ente desktop
app's "Watch folders" feature, then empty the staging folder WITHOUT trashing
anything on Ente.

Workflow
    Lightroom Classic (Hard Drive publish service)
        -> ~/EnteStaging/<Album>/<photos>
        -> ente-sync
        -> photos in Ente album <Album>; staging folder emptied

How it works
    The Ente desktop app persists folder watches in
    ~/Library/Application Support/ente/watch-status.json (electron-store).
    While watching, deleting a file from the folder makes Ente move the
    uploaded copy to the trash. To avoid that, this script:

      1. quits Ente,
      2. adds a temporary watch for the staging folder ("parent" mapping =
         one album per sub-folder),
      3. starts Ente and waits until every staged file is reported as synced
         (uploaded, or already present in that album),
      4. quits Ente, removes the watch entry,
      5. only then deletes the synced files from the staging folder.

    Files that failed or were ignored by Ente are left in place.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path

BUNDLE_ID = "io.ente.bhari-frame"
DEFAULT_STATE_FILE = Path.home() / "Library/Application Support/ente/watch-status.json"
DEFAULT_STAGING = Path.home() / "EnteStaging"

# Mirrors web/apps/photos/src/services/watch.ts (ignoredExtensions) so that we
# know which files Ente will never pick up.
IGNORED_EXTENSIONS = {
    "zip", "rar", "7z", "tar", "gz", "bz2", "txt", "ini", "sfv", "md5",
    "sha1", "sha256", "sha512", "sha", "html", "htm", "xmp", "db", "clpi",
    "mpls", "bdmv", "cmap",
}


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #

def log(msg: str) -> None:
    print(f"[ente-sync {time.strftime('%H:%M:%S')}] {msg}", flush=True)


def die(msg: str, code: int = 1) -> None:
    log(f"ERROR: {msg}")
    sys.exit(code)


def osascript(script: str) -> str:
    return subprocess.run(
        ["osascript", "-e", script], capture_output=True, text=True
    ).stdout.strip()


def ente_running() -> bool:
    return osascript(f'application id "{BUNDLE_ID}" is running') == "true"


def quit_ente(timeout: float = 60) -> None:
    if not ente_running():
        return
    log("Quitting Ente…")
    osascript(f'tell application id "{BUNDLE_ID}" to quit')
    deadline = time.time() + timeout
    while time.time() < deadline:
        if not ente_running():
            time.sleep(1)  # let electron-store finish any pending write
            return
        time.sleep(0.5)
    raise RuntimeError("Ente did not quit; refusing to touch its state or delete files.")


def start_ente() -> None:
    log("Starting Ente…")
    subprocess.run(["open", "-b", BUNDLE_ID], check=True)


def is_candidate(p: Path, root: Path) -> bool:
    """Files Ente's watcher would try to upload."""
    if not p.is_file():
        return False
    if any(part.startswith(".") for part in p.relative_to(root).parts):
        return False
    ext = p.suffix[1:].lower()
    return ext not in IGNORED_EXTENSIONS


def staged_files(staging: Path) -> set[str]:
    return {str(p) for p in staging.rglob("*") if is_candidate(p, staging)}


def read_state(state_file: Path) -> dict:
    if not state_file.exists():
        return {}
    for _ in range(5):
        try:
            data = json.loads(state_file.read_text() or "{}")
            break
        except json.JSONDecodeError:
            time.sleep(0.5)  # may be mid-write
    else:
        raise RuntimeError(f"Cannot parse {state_file}")
    if not isinstance(data, dict):
        raise RuntimeError(f"Unexpected format in {state_file}")
    mappings = data.get("mappings", [])
    if not isinstance(mappings, list) or not all(
        isinstance(m, dict) and isinstance(m.get("folderPath"), str) for m in mappings
    ):
        raise RuntimeError(
            f"Unexpected 'mappings' format in {state_file} "
            "(Ente may have changed its storage format) — aborting safely."
        )
    return data


def write_state(state_file: Path, data: dict) -> None:
    state_file.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=state_file.parent, prefix=".watch-status.", suffix=".tmp")
    with os.fdopen(fd, "w") as f:
        json.dump(data, f, indent="\t")
    os.replace(tmp, state_file)


def find_mapping(data: dict, folder: str) -> dict | None:
    for m in data.get("mappings", []):
        if m.get("folderPath") == folder:
            return m
    return None


def add_watch(state_file: Path, folder: str) -> None:
    data = read_state(state_file)
    if state_file.exists():
        shutil.copy2(state_file, state_file.with_suffix(".json.bak"))
    existing = find_mapping(data, folder)
    if existing:
        if existing.get("collectionMapping") != "parent":
            raise RuntimeError(
                f"A watch for {folder} already exists with mapping "
                f"{existing.get('collectionMapping')!r}; remove it in Ente first."
            )
        log("Reusing existing watch entry for staging folder (previous run interrupted?).")
        return
    data.setdefault("mappings", []).append(
        {
            "collectionMapping": "parent",
            "folderPath": folder,
            "syncedFiles": [],
            "ignoredFiles": [],
        }
    )
    write_state(state_file, data)
    log(f"Added temporary watch for {folder}")


def remove_watch(state_file: Path, folder: str) -> tuple[set[str], set[str]]:
    """Remove our mapping; return (synced paths, ignored paths) it recorded."""
    data = read_state(state_file)
    m = find_mapping(data, folder)
    if not m:
        return set(), set()
    synced = {f["path"] for f in m.get("syncedFiles", []) if isinstance(f, dict) and "path" in f}
    ignored = set(m.get("ignoredFiles", []))
    data["mappings"] = [x for x in data["mappings"] if x is not m]
    write_state(state_file, data)
    log("Removed temporary watch entry.")
    return synced, ignored


def progress(state_file: Path, folder: str, pending: set[str]) -> tuple[set[str], set[str]]:
    m = find_mapping(read_state(state_file), folder) or {}
    synced = {f.get("path") for f in m.get("syncedFiles", []) if isinstance(f, dict)}
    ignored = set(m.get("ignoredFiles", []))
    return synced & pending, ignored & pending


def remove_empty_dirs(root: Path) -> None:
    for d in sorted((p for p in root.rglob("*") if p.is_dir()), key=lambda p: len(p.parts), reverse=True):
        try:
            # Ignore macOS litter when deciding emptiness.
            for junk in (".DS_Store",):
                (d / junk).unlink(missing_ok=True)
            d.rmdir()
        except OSError:
            pass


# --------------------------------------------------------------------------- #
# Main
# --------------------------------------------------------------------------- #

def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--staging", type=Path, default=DEFAULT_STAGING,
                    help=f"Staging folder (default: {DEFAULT_STAGING})")
    ap.add_argument("--idle-timeout", type=int, default=900,
                    help="Give up if no file completes for this many seconds (default: 900)")
    ap.add_argument("--keep-files", action="store_true",
                    help="Upload but do not delete anything from the staging folder")
    ap.add_argument("--no-reopen", action="store_true",
                    help="Leave Ente closed at the end even if it was open before")
    ap.add_argument("--state-file", type=Path, default=DEFAULT_STATE_FILE, help=argparse.SUPPRESS)
    ap.add_argument("--no-app", action="store_true", help=argparse.SUPPRESS)  # testing only
    args = ap.parse_args()

    staging = args.staging.expanduser().resolve()
    if not staging.is_dir():
        die(f"Staging folder {staging} does not exist.")
    folder = str(staging)

    pending = staged_files(staging)
    if not pending:
        log(f"Nothing to upload in {staging}.")
        # Still clean up a stale watch entry from an interrupted run, if any.
        if find_mapping(read_state(args.state_file), folder):
            if not args.no_app:
                quit_ente()
            remove_watch(args.state_file, folder)
        return 0

    root_files = [p for p in pending if Path(p).parent == staging]
    if root_files:
        log(f"WARNING: {len(root_files)} file(s) directly in {staging} will go to an "
            f"album named '{staging.name}'. Put files in sub-folders to choose album names.")

    albums = sorted({Path(p).parent.name for p in pending})
    log(f"{len(pending)} file(s) to upload into {len(albums)} album(s): {', '.join(albums)}")

    was_running = (not args.no_app) and ente_running()
    interrupted = False

    def on_sigint(*_):
        nonlocal interrupted
        interrupted = True
    signal.signal(signal.SIGINT, on_sigint)
    signal.signal(signal.SIGTERM, on_sigint)

    synced: set[str] = set()
    ignored: set[str] = set()
    watch_added = False
    error: Exception | None = None
    try:
        if not args.no_app:
            quit_ente()
        add_watch(args.state_file, folder)
        watch_added = True
        if not args.no_app:
            start_ente()

        last_count, last_change = -1, time.time()
        while not interrupted:
            done_synced, done_ignored = progress(args.state_file, folder, pending)
            count = len(done_synced) + len(done_ignored)
            if count != last_count:
                log(f"Progress: {len(done_synced)} synced, {len(done_ignored)} ignored / {len(pending)}")
                last_count, last_change = count, time.time()
            if count >= len(pending):
                log("All files processed by Ente.")
                break
            if time.time() - last_change > args.idle_timeout:
                log(f"No progress for {args.idle_timeout}s — stopping. "
                    "Unfinished files will be kept for the next run.")
                break
            time.sleep(3)
        if interrupted:
            log("Interrupted — cleaning up safely.")
    except Exception as e:  # noqa: BLE001
        error = e
        log(f"ERROR: {e}")

    if not watch_added:
        # We never touched Ente's state; nothing to undo, nothing to delete.
        if was_running and not args.no_app and not ente_running():
            start_ente()
        return 1

    # Order matters: the app must be closed before we edit its state, and the
    # watch must be gone before any file is deleted.
    try:
        if not args.no_app:
            quit_ente()
        synced, ignored = remove_watch(args.state_file, folder)
    except Exception as e:  # noqa: BLE001
        log(f"ERROR during cleanup: {e}")
        log("NOT deleting any file. Quit Ente, then open it > Watch folders and "
            f"remove the watch for {folder} before emptying the folder.")
        return 2

    # ---- Delete what is safely on Ente ---------------------------------- #
    to_delete = sorted(synced & pending)
    leftover = sorted(pending - synced)
    if args.keep_files:
        log(f"--keep-files: leaving {len(to_delete)} uploaded file(s) in place.")
    else:
        for p in to_delete:
            try:
                os.remove(p)
            except FileNotFoundError:
                pass
        remove_empty_dirs(staging)
        log(f"Deleted {len(to_delete)} uploaded file(s) from staging.")

    if leftover:
        ign = [p for p in leftover if p in ignored]
        failed = [p for p in leftover if p not in ignored]
        if ign:
            log(f"{len(ign)} file(s) were rejected by Ente (unsupported) and left in place:")
            for p in ign:
                log(f"   {p}")
        if failed:
            log(f"{len(failed)} file(s) were NOT uploaded (yet) and left in place; re-run later:")
            for p in failed:
                log(f"   {p}")

    if was_running and not args.no_reopen and not args.no_app:
        start_ente()

    return 1 if error else (0 if not leftover else 3)


if __name__ == "__main__":
    sys.exit(main())
