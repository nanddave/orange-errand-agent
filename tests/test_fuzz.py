import re
import sqlite3
from pathlib import Path

from errand.fuzz import COVERAGE, DEFAULT_RUNS, DEFAULT_SEED, run_many, run_one
from errand.gate import ActionDispatcher, MockBookingProvider
from errand.memory import MemoryStore

ROOT = Path(__file__).resolve().parent.parent


def violated(monkeypatch, patch, runs=80):
    patch(monkeypatch)
    return {inv for report in run_many(runs, 1) for inv, _ in report.violations}


def test_clean_runs_hit_every_fault_and_invariant():
    reports = run_many(60, 1)
    assert [v for r in reports for v in r.violations] == []
    for key in COVERAGE:
        assert sum(r.coverage.get(key, 0) for r in reports) > 0, key
    for key in ("I1", "I2", "I3", "I4", "I5", "I6", "I7", "I8", "I9"):
        assert sum(r.checks.get(key, 0) for r in reports) > 0, key


def test_checker_detects_approval_bypass(monkeypatch):
    def lax_approve(self, action_id, proposal):  # skips the exact-match check
        with sqlite3.connect(self.db) as conn:
            conn.execute("UPDATE actions SET approved = proposal WHERE id = ?", (action_id,))

    found = violated(monkeypatch, lambda m: m.setattr(ActionDispatcher, "approve", lax_approve))
    assert {"I1", "I6"} <= found


def test_checker_detects_double_booking(monkeypatch):
    real = MockBookingProvider.book

    def double_book(self, hold_id, key):
        try:
            return real(self, hold_id, key)
        finally:
            with sqlite3.connect(self.db) as conn:
                conn.execute(
                    "INSERT INTO provider_bookings SELECT lower(hex(randomblob(8))), key || '#dup', hold_id, user_id, "
                    "provider, service, slot, status, price FROM provider_bookings WHERE key = ?", (key,))

    found = violated(monkeypatch, lambda m: m.setattr(MockBookingProvider, "book", double_book))
    assert {"I1", "I2"} <= found


def test_checker_detects_forget_leak(monkeypatch):
    assert "I8" in violated(monkeypatch, lambda m: m.setattr(MemoryStore, "forget", lambda *a, **k: None))


def test_same_seed_same_report(tmp_path):
    (tmp_path / "a").mkdir(), (tmp_path / "b").mkdir()
    assert run_one(7, tmp_path / "a") == run_one(7, tmp_path / "b")


def test_committed_report_is_current():
    text = (ROOT / "FUZZ_REPORT.md").read_text()
    assert f"- Runs: {DEFAULT_RUNS}\n" in text and f"- Seed: {DEFAULT_SEED}\n" in text
    assert "- Total violations: 0\n" in text and "None." in text
    assert f"--runs {DEFAULT_RUNS} --seed {DEFAULT_SEED}" in text
    assert all(int(n) > 0 for n in re.findall(r"^\| (?:Lost|get_|Expired|Proposal|Extra|Provider|Review|Cancel|Reschedule)[^|]*\| (\d+) \|", text, re.M))
