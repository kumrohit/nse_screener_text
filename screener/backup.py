"""Versioned local backup of irreplaceable evidence (ROADMAP Item 18,
v1.0 hardening).

Prices/universe/benchmark stores are explicitly NOT backed up here —
`backfill` regenerates them from scratch, so losing them is an
inconvenience, not a loss. Everything this module covers is either
user-generated (saved screens, watchlist tags) or the record of what
the tool itself concluded at a point in time (screen/allocation/
backtest logs, cohort tracking, including tombstoned cohorts whose
`delete_reason` is itself part of the record) — losing it isn't a
re-fetch, it's gone. `create_backup()` is meant to run nightly
alongside `update && verify`; the off-machine copy step (syncing
`data/backups/` to cloud storage) is a documented manual step, not
automated here — see README's Operations section.
"""
from __future__ import annotations

import datetime as _dt
import json as _json
import shutil
from pathlib import Path

from . import config, universes

BACKUP_DIR = config.DATA_DIR / "backups"
KEEP = 30


def _evidence_files() -> list[Path]:
    """Every file this backup covers, in its current on-disk location —
    silently skips anything that doesn't exist yet (a fresh install, or
    a universe with no cohorts tracked) rather than erroring, since a
    missing file here just means "nothing to back up for that one,"
    not a failure."""
    from . import webapp  # local import: webapp pulls in FastAPI at
                          # module load, heavier than backup.py needs
                          # for its own sake (and creates a real import
                          # cycle if done at module level — webapp.py
                          # doesn't import backup.py, but cli.py imports
                          # both, and backup.py importing webapp.py at
                          # module scope would run FastAPI app setup
                          # just to compute a file list)
    candidates = [
        webapp.LOG_FILE, webapp.ROTATED_LOG_FILE,
        webapp.ALLOCATION_LOG_FILE, webapp.BACKTEST_LOG_FILE,
        webapp.WATCHLIST_FILE, webapp.USER_PRESETS_FILE,
        config.LOCAL_CONFIG_FILE,
    ]
    for uid in universes.UNIVERSES:
        candidates.append(config.cohorts_file(uid))
    return [p for p in candidates if p.exists()]


def create_backup() -> Path:
    """Snapshot every evidence file into data/backups/{timestamp}/. Each
    file keeps its own name except cohorts.jsonl, which is the same
    filename in every universe's directory — prefixed with the universe
    id (its immediate parent directory name) so nifty500's and
    nse_full's copies don't collide in one flat destination."""
    ts = _dt.datetime.now().strftime("%Y%m%dT%H%M%S")
    dest = BACKUP_DIR / ts
    dest.mkdir(parents=True, exist_ok=True)
    for src in _evidence_files():
        name = (f"{src.parent.name}_{src.name}"
               if src.name == "cohorts.jsonl" else src.name)
        shutil.copy2(src, dest / name)
    return dest


def rotate_backups(keep: int = KEEP) -> list[Path]:
    """Delete all but the `keep` most recent snapshots. Directory names
    are zero-padded timestamps, so lexicographic sort is chronological
    sort — no need to parse them back into dates. Returns the removed
    directories."""
    if not BACKUP_DIR.exists():
        return []
    snapshots = sorted(p for p in BACKUP_DIR.iterdir() if p.is_dir())
    to_remove = snapshots[:-keep] if len(snapshots) > keep else []
    for d in to_remove:
        shutil.rmtree(d)
    return to_remove


def latest_backup() -> Path | None:
    if not BACKUP_DIR.exists():
        return None
    snapshots = sorted(p for p in BACKUP_DIR.iterdir() if p.is_dir())
    return snapshots[-1] if snapshots else None


def verify_latest_backup() -> dict:
    """For `verify`: does a backup exist, and do its .jsonl files parse?
    Never raises — a verify check must report a problem, not crash on
    one. Doesn't check .json (USER_PRESETS_FILE) or .toml
    (config_local) contents, only the line-delimited-JSON evidence
    files, since those are the ones a partial/interrupted copy would
    most plausibly corrupt mid-line."""
    latest = latest_backup()
    if latest is None:
        return {"exists": False, "path": None, "bad_files": []}
    bad = []
    for f in sorted(latest.glob("*.jsonl")):
        try:
            for line in f.read_text().strip().splitlines():
                if line:
                    _json.loads(line)
        except (OSError, _json.JSONDecodeError):
            bad.append(f.name)
    return {"exists": True, "path": str(latest), "bad_files": bad}
