"""
Adam — persistent job-store tests (Phase 5).

Proves the SQLite job store does what the in-memory dict could not:

    create -> get · update status (complete/fail) · list recent (+ filter) ·
    restart simulation marks a running job interrupted (never silently complete) ·
    completed + failed jobs survive a restart · wire mapping preserves the PWA
    contract (running/done/error) · delivered bookkeeping · schema versioning ·
    sweep retires old terminal jobs but never an active one.

Self-contained: points the store at a throwaway temp DB; never touches real
data/. Run:  python test_job_store.py   (exit code 0 = all passed)
"""

from __future__ import annotations

import sys
import tempfile
import time
from pathlib import Path

import config
import job_store as js

_passed = 0
_failed = 0


def check(name: str, cond: bool) -> None:
    global _passed, _failed
    if cond:
        _passed += 1
        print(f"  PASS  {name}")
    else:
        _failed += 1
        print(f"  FAIL  {name}")


def _reopen(db: Path) -> None:
    """Simulate a process restart: drop the connection and open the same file."""
    js.close()
    config.JOBS_DB = db
    js.init(db)


def main() -> int:
    sandbox = Path(tempfile.mkdtemp(prefix="jvl_jobs_test_"))
    db = sandbox / "adam.db"
    config.JOBS_DB = db
    config.JOB_INPUT_SUMMARY_MAX = 200

    print("\n[1] Init creates schema + version")
    info = js.init(db)
    check("db created on disk", db.exists())
    check("schema_version is current", info["schema_version"] == js.SCHEMA_VERSION)
    check("reported migration from fresh", info["from_version"] == 0)

    print("\n[2] Create + get a job")
    j = js.create_job("job1", mode="work", session_id="sess-A",
                      input_summary="start my day", pid=4242)
    check("create returns the record", j is not None and j["job_id"] == "job1")
    check("new job is running", j["status"] == js.STATUS_RUNNING)
    check("started_at stamped on running create", bool(j["started_at"]))
    got = js.get_job("job1")
    check("get_job round-trips", got["session_id"] == "sess-A" and got["mode"] == "work")
    check("input summary stored", got["input_summary"] == "start my day")
    check("pid stored", got["pid"] == 4242)
    check("delivered is False initially", got["delivered"] is False)
    check("unknown job returns None", js.get_job("nope") is None)

    print("\n[3] Complete a job (status + outcome persist)")
    js.complete_job("job1", result="Full report on screen.", spoken="Done, sir.",
                    mode="work", session_id="sess-A", ts=1718000000000,
                    proposed_changes=[{"id": "pc1", "action": "create",
                                       "target_path": "x.md", "risk_level": "low",
                                       "summary": "draft"}],
                    notification_status="pushed")
    c = js.get_job("job1")
    check("status -> complete", c["status"] == js.STATUS_COMPLETE)
    check("result persisted", c["result"] == "Full report on screen.")
    check("spoken persisted", c["spoken"] == "Done, sir.")
    check("ts persisted", c["ts"] == 1718000000000)
    check("completed_at stamped", bool(c["completed_at"]))
    check("proposed_changes round-trip as list", c["proposed_changes"][0]["id"] == "pc1")
    check("proposed_change_ids derived", c["proposed_change_ids"] == ["pc1"])
    check("notification_status stored", c["notification_status"] == "pushed")

    print("\n[4] Fail a job")
    js.create_job("job2", mode="voice", session_id="sess-B")
    js.fail_job("job2", "Claude timed out")
    f = js.get_job("job2")
    check("status -> failed", f["status"] == js.STATUS_FAILED)
    check("error persisted", f["error"] == "Claude timed out")
    check("completed_at stamped on failure", bool(f["completed_at"]))

    print("\n[5] List recent (newest first) + status filter")
    js.create_job("job3", mode="voice")  # running
    listed = js.list_jobs(limit=10)
    ids = [r["job_id"] for r in listed]
    check("all three jobs listed", set(ids) == {"job1", "job2", "job3"})
    check("newest first (job3 before job1)", ids.index("job3") < ids.index("job1"))
    running_only = js.list_jobs(status=js.STATUS_RUNNING)
    check("status filter returns only running", [r["job_id"] for r in running_only] == ["job3"])
    check("limit is honored", len(js.list_jobs(limit=1)) == 1)

    print("\n[6] Wire mapping preserves the PWA contract")
    check("complete -> done", js.to_wire(js.get_job("job1"))["status"] == "done")
    check("failed -> error", js.to_wire(js.get_job("job2"))["status"] == "error")
    check("running -> running", js.to_wire(js.get_job("job3"))["status"] == "running")
    wire_done = js.to_wire(js.get_job("job1"))
    check("wire carries result/spoken/ts/session_id",
          wire_done["result"] == "Full report on screen." and wire_done["ts"] == 1718000000000
          and wire_done["session_id"] == "sess-A")
    check("wire carries proposed_changes", wire_done["proposed_changes"][0]["id"] == "pc1")

    print("\n[7] Delivered bookkeeping (job retained, not deleted)")
    js.mark_delivered("job1")
    check("delivered flag set", js.get_job("job1")["delivered"] is True)
    check("job still present after delivery (history)", js.get_job("job1") is not None)

    print("\n[8] Restart simulation — running/queued jobs become interrupted")
    js.create_job("live1", mode="work", session_id="sess-C")          # running
    js.create_job("live2", mode="voice", status=js.STATUS_QUEUED)     # queued
    _reopen(db)  # <-- the process 'restarts' here; no recovery has run yet
    check("running job survived restart as-is (pre-recovery)",
          js.get_job("live1")["status"] == js.STATUS_RUNNING)
    recovered = js.recover_interrupted()
    rec_ids = {r["job_id"] for r in recovered}
    # job3 was also left running back in [5], so it's legitimately recovered too;
    # assert our two new in-flight jobs are among the recovered set.
    check("both in-flight jobs recovered", {"live1", "live2"} <= rec_ids)
    check("running -> interrupted", js.get_job("live1")["status"] == js.STATUS_INTERRUPTED)
    check("queued -> interrupted", js.get_job("live2")["status"] == js.STATUS_INTERRUPTED)
    check("interrupted_at stamped", bool(js.get_job("live1")["interrupted_at"]))
    check("interrupt message preserved", bool(js.get_job("live1")["message"]))
    check("interrupted preserves last-known info (session)",
          js.get_job("live1")["session_id"] == "sess-C")
    check("interrupted maps to wire 'error' with a reason",
          js.to_wire(js.get_job("live1"))["status"] == "error"
          and bool(js.to_wire(js.get_job("live1"))["error"]))
    check("recovery is idempotent (no active jobs left)", js.recover_interrupted() == [])

    print("\n[9] Completed + failed jobs survive a restart")
    _reopen(db)
    check("completed job still complete after restart",
          js.get_job("job1")["status"] == js.STATUS_COMPLETE
          and js.get_job("job1")["result"] == "Full report on screen.")
    check("failed job still failed after restart",
          js.get_job("job2")["status"] == js.STATUS_FAILED)
    check("schema_version intact after restart", js.init(db)["schema_version"] == js.SCHEMA_VERSION)

    print("\n[10] Sweep retires old terminal jobs, never active ones")
    js.create_job("old_done", mode="voice")
    js.complete_job("old_done", result="r", spoken="s", mode="voice",
                    session_id=None, ts=1)
    # Backdate its activity well past any positive TTL.
    js._update("old_done", updated_at_ts=time.time() - 10_000)
    js.create_job("fresh_running", mode="voice")  # active, recent
    deleted = js.sweep(ttl_seconds=3600)
    check("old terminal job swept", js.get_job("old_done") is None and deleted >= 1)
    check("active job never swept", js.get_job("fresh_running") is not None)

    js.close()
    print(f"\n{'=' * 48}")
    print(f"  {_passed} passed, {_failed} failed")
    print(f"  sandbox: {sandbox}")
    print(f"{'=' * 48}\n")
    return 1 if _failed else 0


if __name__ == "__main__":
    sys.exit(main())
