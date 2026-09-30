"""Adam can undo its own file writes, and can target a checklist by name.

Owner report (2026-09-30), after a demo skit: asked to revert, Adam said it had
overwritten a vault file "without reading it first", that "every write here is
backed up automatically" but it "can't operate that restore", and that it needed
the user to read a checklist's number off the screen. Both capabilities existed
underneath (per-change backups; checklist.archive by id) — Adam just couldn't
see or reach them. Pinned here:

  * propose_restore stages the pre-change backup as an ordinary proposal, so a
    restore gets the normal approval posture AND its own backup (restorable too);
  * restoring the EARLIEST of several edits returns the pre-sequence file;
  * a change that created a file restores by deleting it;
  * the <<PROPOSE action="restore" change="…">> block works from a reply;
  * a backup path outside the backups dir is never trusted;
  * the per-turn notes list change ids and checklist ids.
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

os.environ.setdefault("ADAM_CONFIG_ROOT", tempfile.mkdtemp(prefix="jvl_restore_cfg_"))

import config  # noqa: E402

if not config.ADAM_TOKEN:
    config.ADAM_TOKEN = "restore-test-token-" + "r" * 32
if not config.CLAUDE_EXE:
    config.CLAUDE_EXE = sys.executable

import checklist_store as cs  # noqa: E402
import proposed_changes as pc  # noqa: E402
import server  # noqa: E402

REAL_DATA = (REPO_ROOT / "data").resolve()


@pytest.fixture(autouse=True)
def sandbox(tmp_path, monkeypatch):
    """Every store this touches points into tmp — never the live install's data."""
    drafts = tmp_path / "drafts"
    drafts.mkdir()
    monkeypatch.setattr(config, "DRAFTS_DIR", drafts)
    monkeypatch.setattr(config, "PERM_WRITE_DIRS", [str(drafts)])
    monkeypatch.setattr(config, "BACKUP_DIR", tmp_path / "backups")
    monkeypatch.setattr(config, "PROPOSED_CHANGES_FILE", tmp_path / "proposed_changes.json")
    monkeypatch.setattr(config, "AUDIT_LOG_FILE", tmp_path / "audit.jsonl")
    monkeypatch.setattr(config, "CHECKLIST_DB", tmp_path / "checklists.db", raising=False)
    cs.close()
    cs.init(tmp_path / "checklists.db")
    for p in (config.BACKUP_DIR, config.PROPOSED_CHANGES_FILE, config.AUDIT_LOG_FILE,
              config.DRAFTS_DIR):
        assert REAL_DATA not in Path(p).resolve().parents, f"not sandboxed: {p}"
    yield drafts
    cs.close()


def _apply(rec: dict) -> dict:
    pc.approve(rec["id"])
    out, err = pc.apply(rec["id"])
    assert err is None, err
    return out


def test_restore_earliest_edit_returns_the_pre_sequence_file(sandbox):
    f = sandbox / "packet.md"
    f.write_text("ORIGINAL packet\n", encoding="utf-8")
    first = _apply(pc.create(target_path=str(f), action="replace",
                             content="skit v1\n", summary="skit 1"))
    _apply(pc.create(target_path=str(f), action="replace",
                     content="skit v2\n", summary="skit 2"))
    assert f.read_text(encoding="utf-8") == "skit v2\n"

    r = pc.propose_restore(first["id"])
    assert r["status"] == "pending" and r["restores"] == first["id"]
    assert r["action"] == "replace" and r["destructive"]      # normal approval posture
    _apply(r)
    assert f.read_text(encoding="utf-8") == "ORIGINAL packet\n"

    # The restore itself backed up "skit v2", so it is restorable in turn.
    applied = pc.get(r["id"], include_content=False)
    assert applied["backup_path"] and pc.restorable(pc._get_raw(r["id"]))


def test_backup_of_an_old_file_survives_the_prune(sandbox):
    """copy2 kept the SOURCE mtime, so backing up a file untouched for 30+ days had
    its only backup deleted by the prune that runs right after (2026-09-30)."""
    import permissions
    f = sandbox / "old_packet.md"
    f.write_text("from August", encoding="utf-8")
    old = __import__("time").time() - 90 * 86400
    os.utime(f, (old, old))
    edit = _apply(pc.create(target_path=str(f), action="replace", content="skit"))
    assert Path(edit["backup_path"]).is_file()
    assert permissions.prune_backups() == 0
    r = pc.propose_restore(edit["id"])
    _apply(r)
    assert f.read_text(encoding="utf-8") == "from August"


def test_restore_of_a_created_file_removes_it(sandbox):
    f = sandbox / "new_note.md"
    made = _apply(pc.create(target_path=str(f), action="create", content="hi\n"))
    assert f.is_file()
    r = pc.propose_restore(made["id"])
    assert r["action"] == "delete"
    _apply(r)
    assert not f.exists()


def test_restore_refuses_unknown_pending_and_foreign_backup(sandbox, tmp_path):
    with pytest.raises(ValueError):
        pc.propose_restore("nope")
    f = sandbox / "a.md"
    f.write_text("x", encoding="utf-8")
    pending = pc.create(target_path=str(f), action="replace", content="y")
    with pytest.raises(ValueError):
        pc.propose_restore(pending["id"])                   # never applied
    # A record whose backup_path points OUTSIDE the backups dir is not trusted.
    outside = tmp_path / "elsewhere.md"
    outside.write_text("attacker content", encoding="utf-8")
    rec = {"status": "applied", "action": "replace", "backup_path": str(outside),
           "target_path": str(f)}
    assert pc.restorable(rec) is False


def test_restore_block_in_a_reply_stages_the_restore(sandbox):
    f = sandbox / "plan.md"
    f.write_text("before\n", encoding="utf-8")
    edit = _apply(pc.create(target_path=str(f), action="replace", content="after\n"))
    text = ('Putting it back.\n<<PROPOSE action="restore" change="%s" summary="undo skit">>'
            "<<END_PROPOSE>>" % edit["id"])
    cleaned, recs = pc.extract_from_reply(text)
    assert cleaned == "Putting it back."
    assert len(recs) == 1 and recs[0]["restores"] == edit["id"]
    assert recs[0]["summary"] == "undo skit"
    # An unknown id stages nothing (and never raises).
    _, none = pc.extract_from_reply('<<PROPOSE action="restore" change="zzz">><<END_PROPOSE>>')
    assert none == []


def test_recent_changes_note_lists_ids_and_restorability(sandbox):
    f = sandbox / "packet.md"
    f.write_text("orig", encoding="utf-8")
    edit = _apply(pc.create(target_path=str(f), action="replace", content="new",
                            summary="Weekend plan packet"))
    note = server._recent_changes_note(str(sandbox), auto_apply=False)
    assert edit["id"] in note and "packet.md" in note and "[restorable]" in note
    assert 'action="restore"' in note and "EARLIEST" in note
    assert "part of the way back" in note          # partial-restore honesty
    assert "waits for the user's approval" in note
    assert "applies immediately" in server._recent_changes_note(str(sandbox), auto_apply=True)


def test_same_second_changes_list_newest_first(sandbox):
    f = sandbox / "same.md"
    f.write_text("0", encoding="utf-8")
    a = _apply(pc.create(target_path=str(f), action="replace", content="1"))
    b = _apply(pc.create(target_path=str(f), action="replace", content="2"))
    ids = [r["id"] for r in pc.recent_applied()]
    assert ids.index(b["id"]) < ids.index(a["id"])


def test_recent_changes_note_empty_when_nothing_applied(sandbox):
    assert server._recent_changes_note(str(sandbox)) == ""


def test_checklists_note_lists_active_ids_only(sandbox):
    keep = cs.create_checklist(title="Weekend skit list", items=[{"text": "a"}])
    gone = cs.create_checklist(title="Old list")
    cs.archive_checklist(gone)
    note = server._checklists_note()
    assert f"id {keep} · Weekend skit list (0/1 done)" in note
    assert "Old list" not in note
    assert "never ask the user for a list's number" in note


def test_checklists_note_empty_without_lists(sandbox):
    assert server._checklists_note() == ""


# --- system prompt rides a file, not the Windows command line ------------------
# The argv prompt had reached ~28.7k of Windows' 32,767-char cap; these notes would
# have pushed a busy turn over it and failed the spawn outright.

import asyncio  # noqa: E402


def _capture_turn(monkeypatch, tmp_path, supports: bool, fail_spawn: bool = False):
    monkeypatch.setattr(config, "STATE_DIR", tmp_path / "state")
    exe = str(config.CLAUDE_EXE)
    monkeypatch.setitem(server._PROMPT_FILE_SUPPORT, exe, supports)
    seen = {}

    class _Stop(Exception):
        pass

    async def fake_exec(*cmd, **kw):
        seen["cmd"] = list(cmd)
        if "--append-system-prompt-file" in cmd:
            f = Path(cmd[cmd.index("--append-system-prompt-file") + 1])
            seen["file"] = f
            seen["text"] = f.read_text(encoding="utf-8")   # exists while the CLI runs
        raise (OSError("spawn failed") if fail_spawn else _Stop())

    monkeypatch.setattr(server.asyncio, "create_subprocess_exec", fake_exec)
    try:
        asyncio.run(server.run_claude("hello there", None, mode="voice"))
    except (_Stop, OSError):
        pass
    return seen


def test_prompt_goes_in_a_file_and_is_deleted(monkeypatch, tmp_path):
    seen = _capture_turn(monkeypatch, tmp_path, supports=True)
    cmd = seen["cmd"]
    assert "--append-system-prompt" not in cmd            # no giant argv prompt
    assert "YOUR RECENT FILE CHANGES" in seen["text"] or "EXTERNAL ACTIONS" in seen["text"]
    assert cmd[-1] == "hello there"                        # short message back on argv
    assert sum(len(a) + 1 for a in cmd) < 8000
    assert not seen["file"].exists()                       # cleaned up after the turn


def test_prompt_file_removed_even_when_spawn_fails(monkeypatch, tmp_path):
    seen = _capture_turn(monkeypatch, tmp_path, supports=True, fail_spawn=True)
    assert not seen["file"].exists()


def test_old_cli_keeps_the_argv_prompt(monkeypatch, tmp_path):
    seen = _capture_turn(monkeypatch, tmp_path, supports=False)
    assert "--append-system-prompt" in seen["cmd"] and "file" not in seen
