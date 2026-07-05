"""
Adam — proposed-change / safe-agent-mode tests (Phase 3).

Self-contained: redirects the policy at a temp sandbox and proves the
controlled-write flow is the real enforcement boundary:

    create -> approve/deny -> apply · backup before overwrite ·
    denied changes are never applied · blocked + protected targets are refused ·
    destructive changes require approval · default config is the safe mode ·
    <<PROPOSE>> extraction routes into the drafts area.

Run:  python test_proposed_changes.py   (exit code 0 = all passed)
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

import json

import config
import diffs
import permissions
import proposed_changes as pc

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


def main() -> int:
    sandbox = Path(tempfile.mkdtemp(prefix="jvl_pc_test_"))
    vault = sandbox / "vault"
    drafts = sandbox / "drafts"
    outputs = sandbox / "outputs"
    blocked = sandbox / "secret_zone"
    backups = sandbox / "backups"
    state = sandbox / "state"
    logs = sandbox / "logs"
    for d in (vault, drafts, outputs, blocked, backups, state, logs):
        d.mkdir(parents=True, exist_ok=True)

    config.PERM_READ_DIRS = [str(vault)]
    config.PERM_WRITE_DIRS = [str(drafts), str(outputs)]
    config.INFRA_WRITE_DIRS = [str(state), str(logs), str(backups)]
    config.PERM_BLOCKED_PATHS = [str(blocked)]
    config.PERM_PROTECTED_FILE_PATTERNS = [".env", "*.pem", "settings.json"]
    config.PERM_MAX_FILE_SIZE_MB = 1
    config.PERM_REQUIRE_APPROVAL_WRITES = True
    config.PERM_REQUIRE_APPROVAL_DESTRUCTIVE = True
    config.PERM_BACKUP_BEFORE_WRITE = True
    config.PERM_AUDIT_LOG_ENABLED = True
    config.BACKUP_DIR = backups
    config.AUDIT_LOG_FILE = logs / "audit.jsonl"
    config.DRAFTS_DIR = drafts
    config.OUTPUTS_DIR = outputs
    config.PROPOSED_CHANGES_FILE = state / "proposed_changes.json"
    config.FORCE_OUTPUTS_TO_DRAFTS = True

    print("\n[1] Default config is the safe mode")
    # Re-derived from the shipped settings at import; assert the safe posture.
    check("AGENT_MODE defaults to draft_only", config.AGENT_MODE == "draft_only")
    check("direct Claude writes disabled by default", config.AGENT_DIRECT_WRITES is False)
    check("tools restricted in safe mode", config.AGENT_RESTRICT_TOOLS is True)
    check("mutators are in the denied-tools list",
          all(t in config.AGENT_DENIED_TOOLS for t in ("Write", "Edit", "Bash")))

    print("\n[2] Create + approve + apply (happy path)")
    r = pc.create(target_path="report.md", action="create", content="v1 content",
                  summary="draft report")
    cid = r["id"]
    check("created pending", r["status"] == "pending")
    check("relative target routed under drafts", str(drafts) in r["target_path"])
    check("apply BEFORE approval is refused", pc.apply(cid)[1] is not None)
    pc.approve(cid)
    rec, err = pc.apply(cid)
    check("apply after approval succeeds", err is None and rec["status"] == "applied")
    check("file written with content",
          (drafts / "report.md").is_file() and (drafts / "report.md").read_text("utf-8") == "v1 content")

    print("\n[3] Backup before overwrite")
    r2 = pc.create(target_path="report.md", action="replace", content="v2 content",
                   summary="update report")
    check("overwrite flagged destructive", r2["destructive"] is True)
    pc.approve(r2["id"])
    rec2, err2 = pc.apply(r2["id"])
    check("overwrite applied", err2 is None and (drafts / "report.md").read_text("utf-8") == "v2 content")
    check("backup created before overwrite",
          rec2["backup_path"] and Path(rec2["backup_path"]).read_text("utf-8") == "v1 content")

    print("\n[4] Denied changes are never applied")
    r3 = pc.create(target_path="nope.md", action="create", content="should not exist")
    pc.deny(r3["id"])
    rec3, err3 = pc.apply(r3["id"])
    check("denied change refuses to apply", err3 is not None)
    check("denied change file not written", not (drafts / "nope.md").exists())

    print("\n[5] Blocked + protected + out-of-allow-list refused at CREATE (F2)")

    def _create_rejected(name: str, **kw) -> None:
        try:
            pc.create(**kw)
            check(name, False)  # should have raised before returning
        except ValueError:
            check(name, True)

    _create_rejected("blocked-path create rejected",
                     target_path=str(blocked / "x.md"), action="create", content="x")
    check("blocked file not written", not (blocked / "x.md").exists())

    _create_rejected("protected filename create rejected",
                     target_path=str(drafts / ".env"), action="create", content="SECRET=1")
    check("protected file not written", not (drafts / ".env").exists())

    _create_rejected("out-of-allow-list (vault) create rejected",
                     target_path=str(vault / "real.md"), action="create", content="hi")
    check("vault file not written", not (vault / "real.md").exists())

    print("\n[5b] Apply-time validation still enforced independently of create")
    # A proposal to an allowed target that becomes disallowed before apply must
    # STILL be refused at apply — proving create-time validation did not replace
    # the apply-time gate (defense in depth).
    guarded = pc.create(target_path="guarded.md", action="create", content="g")
    pc.approve(guarded["id"])
    _saved_write_dirs = config.PERM_WRITE_DIRS
    config.PERM_WRITE_DIRS = []  # policy tightened after the proposal was made
    try:
        rec_g, err_g = pc.apply(guarded["id"])
        check("apply still refuses when policy no longer allows the target",
              err_g is not None and rec_g["status"] == "failed")
        check("guarded file not written under tightened policy",
              not (drafts / "guarded.md").exists())
    finally:
        config.PERM_WRITE_DIRS = _saved_write_dirs

    print("\n[6] Destructive change requires approval")
    (drafts / "victim.md").write_text("delete me", encoding="utf-8")
    rd = pc.create(target_path=str(drafts / "victim.md"), action="delete")
    check("delete flagged destructive", rd["destructive"] is True)
    check("delete risk is high", rd["risk_level"] == "high")
    recd, errd = pc.apply(rd["id"])  # not approved yet
    check("unapproved delete refused", errd is not None and (drafts / "victim.md").exists())
    pc.approve(rd["id"])
    recd2, errd2 = pc.apply(rd["id"])
    check("approved delete applies + backs up",
          errd2 is None and not (drafts / "victim.md").exists() and recd2["backup_path"])

    print("\n[7] Rename + content size cap")
    (drafts / "old.md").write_text("body", encoding="utf-8")
    rr = pc.create(target_path=str(drafts / "old.md"), action="rename",
                   new_path=str(drafts / "new.md"))
    pc.approve(rr["id"])
    recr, errr = pc.apply(rr["id"])
    check("rename moves file", errr is None and (drafts / "new.md").exists() and not (drafts / "old.md").exists())
    big = pc.create(target_path="big.bin", action="create", content="x" * (2 * 1024 * 1024))
    pc.approve(big["id"])
    _, errbig = pc.apply(big["id"])
    check("oversize content refused", errbig is not None and "max_file_size" in errbig)

    print("\n[8] <<PROPOSE>> extraction")
    reply = ('Here is my plan.\n'
             '<<PROPOSE path="notes.md" action="create" risk="low" summary="add notes">>\n'
             'note body line\n'
             '<<END_PROPOSE>>\n'
             'Done.')
    cleaned, recs = pc.extract_from_reply(reply, session_id="s1")
    check("one proposed change extracted", len(recs) == 1)
    check("block removed from cleaned reply", "<<PROPOSE" not in cleaned and "Done." in cleaned)
    check("extracted target routed under drafts", str(drafts) in recs[0]["target_path"])
    pc.approve(recs[0]["id"])
    _, erre = pc.apply(recs[0]["id"])
    check("extracted change applies", erre is None and (drafts / "notes.md").read_text("utf-8").strip() == "note body line")

    # ===================== Phase 4 — Trustworthy Change Review =====================

    print("\n[9] Diff + hashes — new file")
    n = pc.create(target_path="fresh.md", action="create",
                  content="line one\nline two\n", summary="new file")
    check("new-file diff available", n["diff_available"] is True)
    check("new-file diff shows additions", bool(n["diff"]) and "+line one" in n["diff"])
    check("new-file original_hash is null (was absent)", n["original_hash"] is None)
    check("proposed_hash is a sha256", isinstance(n["proposed_hash"], str) and len(n["proposed_hash"]) == 64)
    check("proposed_size stored", n["proposed_size"] == len("line one\nline two\n".encode("utf-8")))

    print("\n[10] Diff + hashes — existing file replace/edit")
    (drafts / "exist.md").write_text("alpha\nbeta\n", encoding="utf-8")
    e = pc.create(target_path=str(drafts / "exist.md"), action="replace",
                  content="alpha\nGAMMA\n")
    check("replace diff available", e["diff_available"] is True)
    check("replace diff shows removal", "-beta" in e["diff"])
    check("replace diff shows addition", "+GAMMA" in e["diff"])
    check("replace original_hash captured (sha256)",
          isinstance(e["original_hash"], str) and len(e["original_hash"]) == 64)
    check("replace original_size matches on-disk bytes",
          e["original_size"] == diffs.hash_and_size_of_file(drafts / "exist.md")[1])

    print("\n[11] Diff unavailable — binary + too-large")
    (drafts / "bin.dat").write_bytes(b"PK\x03\x04\x00\x01\x02BIN")
    b = pc.create(target_path=str(drafts / "bin.dat"), action="replace", content="text")
    check("binary diff unavailable", b["diff_available"] is False)
    check("binary reason explains why", bool(b["diff_unavailable_reason"]) and "binary" in b["diff_unavailable_reason"])
    check("binary original_hash still captured (for conflict checks)",
          isinstance(b["original_hash"], str))
    big_path = drafts / "huge.txt"
    big_path.write_text("A" * (2 * 1024 * 1024), encoding="utf-8")  # > 1 MB cap
    hg = pc.create(target_path=str(big_path), action="replace", content="small")
    check("too-large diff unavailable", hg["diff_available"] is False)
    check("too-large reason mentions size", bool(hg["diff_unavailable_reason"]) and "large" in hg["diff_unavailable_reason"])

    print("\n[12] Stale target — conflict blocks apply, refresh recovers")
    fp = drafts / "stale.md"
    fp.write_text("v1\n", encoding="utf-8")
    st = pc.create(target_path=str(fp), action="replace", content="v2 proposed\n")
    cid = st["id"]
    check("baseline original_hash captured", isinstance(st["original_hash"], str))
    fp.write_text("v1 edited by the user\n", encoding="utf-8")  # changed underneath
    pc.approve(cid)
    rec_c, err_c = pc.apply(cid)
    check("apply blocked when target changed", err_c is not None and rec_c["status"] == "conflict")
    check("conflict_reason recorded", bool(rec_c["conflict_reason"]))
    check("user edit NOT overwritten", fp.read_text("utf-8") == "v1 edited by the user\n")
    rf = pc.refresh_diff(cid)
    check("refresh resets to pending", rf["status"] == "pending")
    check("refresh re-baselines to current file",
          rf["original_hash"] == diffs.hash_and_size_of_file(fp)[0])
    pc.approve(cid)
    rec_ok, err_ok = pc.apply(cid)
    check("apply succeeds after refresh + re-approve", err_ok is None and rec_ok["status"] == "applied")
    check("proposed content now on disk", fp.read_text("utf-8") == "v2 proposed\n")

    print("\n[13] Apply succeeds when hash matches (+ audit events)")
    mp = drafts / "match.md"
    mp.write_text("base\n", encoding="utf-8")
    m = pc.create(target_path=str(mp), action="replace", content="base2\n")
    pc.approve(m["id"])
    rec_m, err_m = pc.apply(m["id"])
    check("apply succeeds when unchanged since proposal",
          err_m is None and rec_m["status"] == "applied")
    check("backup created before matched overwrite", bool(rec_m["backup_path"]))
    audit_text = (logs / "audit.jsonl").read_text("utf-8")
    check("audit recorded a conflict event", "proposed_change_conflict" in audit_text)
    check("audit recorded an applied event", "proposed_change_applied" in audit_text)

    print("\n[14] Legacy record (no baseline) applies gracefully")
    lf = drafts / "legacy.md"
    lf.write_text("orig\n", encoding="utf-8")
    lg = pc.create(target_path=str(lf), action="replace", content="new legacy\n")
    # Simulate a pre-Phase-4 record by stripping the baseline hash field.
    store = config.PROPOSED_CHANGES_FILE
    data = json.loads(Path(store).read_text("utf-8"))
    for it in data:
        if it["id"] == lg["id"]:
            it.pop("original_hash", None)
    Path(store).write_text(json.dumps(data, indent=2), encoding="utf-8")
    lf.write_text("changed underneath\n", encoding="utf-8")  # would conflict if checked
    pc.approve(lg["id"])
    rec_l, err_l = pc.apply(lg["id"])
    check("legacy record applies (staleness skipped, no baseline)",
          err_l is None and rec_l["status"] == "applied")

    print("\n[15] status_preview reports live staleness without mutating")
    sp_path = drafts / "preview.md"
    sp_path.write_text("p1\n", encoding="utf-8")
    sp = pc.create(target_path=str(sp_path), action="replace", content="p2\n")
    prev_clean = pc.status_preview(sp["id"])
    check("preview not stale when unchanged", prev_clean["stale"] is False)
    sp_path.write_text("p1 changed\n", encoding="utf-8")
    prev_stale = pc.status_preview(sp["id"])
    check("preview detects staleness", prev_stale["stale"] is True and bool(prev_stale["stale_reason"]))
    check("status_preview did not mutate the record",
          pc.get(sp["id"])["status"] == "pending")

    print("\n[16] F1 — relative path already inside a write dir is not doubled")
    # With the project root pointed at the sandbox, a relative path that names the
    # drafts dir must resolve to <drafts>/inside.md, NOT <drafts>/drafts/inside.md.
    _saved_root = config.ROOT
    config.ROOT = sandbox
    try:
        f1 = pc.create(target_path="drafts/inside.md", action="create", content="hi")
        check("relative-in-writedir honored (no doubling)",
              Path(f1["target_path"]) == (drafts / "inside.md").resolve())
    finally:
        config.ROOT = _saved_root
    # And the safe default still holds: a bare name nests under drafts.
    f1b = pc.create(target_path="bare.md", action="create", content="hi")
    check("bare relative name still nests under drafts",
          Path(f1b["target_path"]) == (drafts / "bare.md").resolve())

    print("\n[17] Internal <<PROPOSE>> path skips a rejected target without raising")
    reply2 = (
        'Plan.\n'
        '<<PROPOSE path=".env" action="create" summary="bad">>\n'
        'SECRET=1\n<<END_PROPOSE>>\n'
        '<<PROPOSE path="good.md" action="create" summary="ok">>\n'
        'fine\n<<END_PROPOSE>>\nDone.'
    )
    cleaned2, recs2 = pc.extract_from_reply(reply2, session_id="s2")
    check("rejected proposal skipped, not raised (1 of 2 survives)", len(recs2) == 1)
    check("the surviving proposal is the allowed one",
          recs2 and recs2[0]["target_path"].endswith("good.md"))
    check("no protected .env proposal created",
          all(not r["target_path"].endswith(".env") for r in recs2))

    print("\n[18] Brain self-write lane — routing (absolute vault paths) + predicate")
    config.VAULT_PATH = str(vault)
    config.BRAIN_WRITE_ENABLED = True
    abs_brain = str(vault / "02_command_memory" / "ltm.md")
    reply_b = (
        f'<<PROPOSE path="{abs_brain}" action="create" risk="low" summary="brain update">>\n'
        'remember this\n<<END_PROPOSE>>\n'
        '<<PROPOSE path="scratch.md" action="create" summary="scratch">>\n'
        'temp\n<<END_PROPOSE>>'
    )
    _, recs_b = pc.extract_from_reply(reply_b, session_id="sb")
    by = {r["summary"]: r for r in recs_b}
    check("absolute vault path preserved into the brain",
          str(vault) in by["brain update"]["target_path"]
          and str(drafts) not in by["brain update"]["target_path"])
    check("relative scratch path still routed to drafts",
          str(drafts) in by["scratch"]["target_path"])

    abs_out = str(outputs / "out.md")  # absolute, but OUTSIDE the vault
    _, recs_o = pc.extract_from_reply(
        f'<<PROPOSE path="{abs_out}" action="create" summary="outside">>\nx\n<<END_PROPOSE>>',
        session_id="so")
    check("absolute non-vault path flattened to drafts",
          str(drafts) in recs_o[0]["target_path"])

    config.BRAIN_WRITE_ENABLED = False
    _, recs_off = pc.extract_from_reply(
        f'<<PROPOSE path="{abs_brain}" action="create" summary="off">>\ny\n<<END_PROPOSE>>',
        session_id="soff")
    check("lane OFF -> even a vault path flattens to drafts",
          str(drafts) in recs_off[0]["target_path"])

    config.BRAIN_WRITE_ENABLED = True
    check("predicate: non-destructive in-vault IS a brain self-write",
          pc.is_brain_self_write({"target_path": abs_brain, "destructive": False}))
    check("predicate: destructive in-vault is NOT (stays gated)",
          not pc.is_brain_self_write({"target_path": abs_brain, "destructive": True}))
    check("predicate: out-of-vault is NOT a brain self-write",
          not pc.is_brain_self_write({"target_path": str(outputs / "x.md"), "destructive": False}))
    config.BRAIN_WRITE_ENABLED = False
    check("predicate: disabled lane is never a brain self-write",
          not pc.is_brain_self_write({"target_path": abs_brain, "destructive": False}))

    print("\n[19] Dismiss clears a change from the panel store (any status)")
    config.BRAIN_WRITE_ENABLED = False
    da = pc.create(target_path="dismiss_me.md", action="create", content="x")
    check("created change is in the store", pc.get(da["id"]) is not None)
    removed = pc.dismiss(da["id"])
    check("dismiss returns the removed record", removed is not None and removed["id"] == da["id"])
    check("dismissed change is gone from the store", pc.get(da["id"]) is None)
    check("dismissing an unknown id returns None", pc.dismiss("nope123abc") is None)
    db = pc.create(target_path="dismiss_me2.md", action="create", content="y")
    pc.approve(db["id"]); pc.apply(db["id"])
    pc.dismiss(db["id"])
    check("an applied change can also be cleared", pc.get(db["id"]) is None)
    check("dismiss is audited",
          "proposed_change_dismissed" in (logs / "audit.jsonl").read_text("utf-8"))

    print(f"\n{'=' * 48}")
    print(f"  {_passed} passed, {_failed} failed")
    print(f"  sandbox: {sandbox}")
    print(f"{'=' * 48}\n")
    return 1 if _failed else 0


if __name__ == "__main__":
    sys.exit(main())
