"""Tests for the evidence backup mechanism (ROADMAP Item 18, v1.0
hardening — screener/backup.py)."""
from __future__ import annotations

import json
import time

import pytest

from screener import backup, config, webapp


@pytest.fixture
def tmp_data_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    # webapp's log/store constants are bound once at import time, so
    # patching config.DATA_DIR alone doesn't redirect them — same
    # reasoning as tests/conftest.py's autouse isolation fixture, but
    # pinned to the exact filenames this test's _seed_evidence() writes
    # (the autouse fixture uses its own arbitrary tmp_path/<attr-name>
    # naming, which wouldn't line up with what we seed by hand here).
    monkeypatch.setattr(webapp, "LOG_FILE", tmp_path / "screen_log.jsonl")
    monkeypatch.setattr(webapp, "ROTATED_LOG_FILE",
                        tmp_path / "screen_log.rotated.jsonl")
    monkeypatch.setattr(webapp, "ALLOCATION_LOG_FILE",
                        tmp_path / "allocation_log.jsonl")
    monkeypatch.setattr(webapp, "BACKTEST_LOG_FILE",
                        tmp_path / "backtest_log.jsonl")
    monkeypatch.setattr(webapp, "WATCHLIST_FILE", tmp_path / "watchlist.jsonl")
    monkeypatch.setattr(webapp, "USER_PRESETS_FILE",
                        tmp_path / "user_presets.json")
    monkeypatch.setattr(backup, "BACKUP_DIR", tmp_path / "backups")
    return tmp_path


def _seed_evidence(data_dir, universe="nifty500"):
    (data_dir / universe).mkdir(parents=True, exist_ok=True)
    (data_dir / universe / "cohorts.jsonl").write_text(
        json.dumps({"cohort_id": "c1"}) + "\n")
    (data_dir / "screen_log.jsonl").write_text(
        json.dumps({"ts": "x"}) + "\n")
    (data_dir / "watchlist.jsonl").write_text(
        json.dumps({"symbol": "A"}) + "\n")
    (data_dir / "user_presets.json").write_text("[]")


class TestCreateBackup:
    def test_backs_up_only_existing_evidence_files(self, tmp_data_dir):
        _seed_evidence(tmp_data_dir)
        dest = backup.create_backup()
        files = {p.name for p in dest.iterdir()}
        assert "nifty500_cohorts.jsonl" in files
        assert "screen_log.jsonl" in files
        assert "watchlist.jsonl" in files
        assert "user_presets.json" in files
        # never-seeded files are simply absent, not an error
        assert "allocation_log.jsonl" not in files

    def test_disambiguates_cohorts_across_universes(self, tmp_data_dir):
        _seed_evidence(tmp_data_dir, "nifty500")
        _seed_evidence(tmp_data_dir, "nse_full")
        dest = backup.create_backup()
        files = {p.name for p in dest.iterdir()}
        assert "nifty500_cohorts.jsonl" in files
        assert "nse_full_cohorts.jsonl" in files

    def test_copy_is_byte_identical(self, tmp_data_dir):
        _seed_evidence(tmp_data_dir)
        dest = backup.create_backup()
        original = (tmp_data_dir / "screen_log.jsonl").read_text()
        copied = (dest / "screen_log.jsonl").read_text()
        assert original == copied

    def test_does_not_back_up_prices_or_universe_files(self, tmp_data_dir):
        _seed_evidence(tmp_data_dir)
        (tmp_data_dir / "nifty500" / "prices.parquet").write_bytes(b"x")
        (tmp_data_dir / "nifty500" / "universe.csv").write_text("symbol\n")
        dest = backup.create_backup()
        files = {p.name for p in dest.iterdir()}
        assert not any("prices" in f or "universe.csv" in f for f in files)


class TestRotateBackups:
    def test_keeps_only_the_n_most_recent(self, tmp_data_dir):
        _seed_evidence(tmp_data_dir)
        for _ in range(5):
            backup.create_backup()
            time.sleep(1.01)  # timestamp granularity is 1 second
        removed = backup.rotate_backups(keep=2)
        remaining = sorted(p.name for p in backup.BACKUP_DIR.iterdir())
        assert len(remaining) == 2
        assert len(removed) == 3
        assert remaining == sorted(remaining)[-2:]

    def test_no_op_when_under_the_limit(self, tmp_data_dir):
        _seed_evidence(tmp_data_dir)
        backup.create_backup()
        removed = backup.rotate_backups(keep=30)
        assert removed == []
        assert len(list(backup.BACKUP_DIR.iterdir())) == 1

    def test_no_op_when_no_backups_exist_yet(self, tmp_data_dir):
        assert backup.rotate_backups() == []


class TestVerifyLatestBackup:
    def test_reports_missing_when_none_exists(self, tmp_data_dir):
        info = backup.verify_latest_backup()
        assert info == {"exists": False, "path": None, "bad_files": []}

    def test_reports_healthy_backup(self, tmp_data_dir):
        _seed_evidence(tmp_data_dir)
        backup.create_backup()
        info = backup.verify_latest_backup()
        assert info["exists"] is True
        assert info["bad_files"] == []

    def test_flags_corrupted_jsonl_in_the_backup(self, tmp_data_dir):
        _seed_evidence(tmp_data_dir)
        dest = backup.create_backup()
        (dest / "screen_log.jsonl").write_text("{not valid json\n")
        info = backup.verify_latest_backup()
        assert info["exists"] is True
        assert "screen_log.jsonl" in info["bad_files"]

    def test_reports_the_most_recent_of_several(self, tmp_data_dir):
        _seed_evidence(tmp_data_dir)
        backup.create_backup()
        time.sleep(1.01)
        dest2 = backup.create_backup()
        info = backup.verify_latest_backup()
        assert info["path"] == str(dest2)


class TestCheckBackupVerifyRow:
    def test_warn_when_missing(self):
        from screener import verify
        name, status, detail = verify.check_backup(
            {"exists": False, "path": None, "bad_files": []})
        assert status == verify.WARN

    def test_fail_when_bad_files(self):
        from screener import verify
        name, status, detail = verify.check_backup(
            {"exists": True, "path": "/x", "bad_files": ["screen_log.jsonl"]})
        assert status == verify.FAIL

    def test_pass_when_healthy(self):
        from screener import verify
        name, status, detail = verify.check_backup(
            {"exists": True, "path": "/x", "bad_files": []})
        assert status == verify.PASS
