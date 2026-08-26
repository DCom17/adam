"""Native pytest coverage for GET /export — the whole-data backup.

data/backups held per-file pre-write copies, which is an undo, not a backup:
nothing covered "the machine is gone". The trackers are the sharp edge — months
of finance and health entries living in one SQLite file each.

Pinned here:
  * token-gated;
  * the irreplaceable things are in (databases, state, settings);
  * the things that must never travel are out (.env, private keys, logs, old
    backups, uploads) — this archive is meant to be safe on a USB stick;
  * databases go through sqlite3's backup API, so what lands in the ZIP is a
    consistent database and not a torn file copy;
  * a single unreadable database degrades to a skip instead of failing the
    whole export.
"""

from __future__ import annotations

import io
import os
import sqlite3
import sys
import tempfile
import zipfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

os.environ.setdefault("ADAM_CONFIG_ROOT", tempfile.mkdtemp(prefix="jvl_export_cfg_"))

import config  # noqa: E402

if not config.ADAM_TOKEN:
    config.ADAM_TOKEN = "export-test-token-" + "e" * 32
if not config.CLAUDE_EXE:
    config.CLAUDE_EXE = sys.executable

import server  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

client = TestClient(server.app)
AUTH = {"Authorization": f"Bearer {config.ADAM_TOKEN}"}


def _fixture_data_dir() -> Path:
    """A data tree with one of everything the walker has to decide about."""
    root = Path(tempfile.mkdtemp(prefix="jvl_export_data_"))

    (root / "state").mkdir(parents=True)
    (root / "state" / "ui_prefs.json").write_text('{"auto_apply_proposed": false}', "utf-8")
    (root / "state" / "trial_start.txt").write_text("2026-01-01", "utf-8")
    # Must NOT travel: a private key and an env file inside the data tree.
    (root / "state" / "vapid_private.pem").write_text("-----BEGIN EC PRIVATE KEY-----", "utf-8")
    (root / ".env").write_text("ANTHROPIC_API_KEY=sk-ant-should-never-ship", "utf-8")

    # A real database with a row we can look for on the other side.
    db = root / "state" / "adam.db"
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE jobs (id TEXT, status TEXT)")
    con.execute("INSERT INTO jobs VALUES ('j1', 'complete')")
    con.commit()
    con.close()

    (root / "finance").mkdir()
    fdb = root / "finance" / "finance.db"
    con = sqlite3.connect(fdb)
    con.execute("CREATE TABLE txns (id INTEGER, amount REAL)")
    con.execute("INSERT INTO txns VALUES (1, -42.5)")
    con.commit()
    con.close()

    # Excluded directories. baseline/ is the updater's merge reference — a full
    # copy of the install tree, and the reason a first real export weighed 603 MB.
    for sub, name in [("logs", "voice_server.log"), ("backups", "20260101_x.md"),
                      ("uploads", "photo.png"), ("agent_workspace", "scratch.txt"),
                      ("baseline", "server.py"), ("baseline.tmp", "adam-setup.exe")]:
        (root / sub).mkdir()
        (root / sub / name).write_text("should not be exported", "utf-8")

    return root


def _export_zip() -> zipfile.ZipFile:
    r = client.get("/export", headers=AUTH)
    assert r.status_code == 200, r.text
    assert r.headers["content-type"] == "application/zip"
    return zipfile.ZipFile(io.BytesIO(r.content))


def _with_fixture(fn):
    """Point config at a throwaway data tree for one call."""
    real_data, real_root = config.DATA_DIR, config.CONFIG_ROOT
    data = _fixture_data_dir()
    cfg_root = Path(tempfile.mkdtemp(prefix="jvl_export_root_"))
    (cfg_root / "settings.json").write_text('{"voice_model": "default"}', "utf-8")
    config.DATA_DIR, config.CONFIG_ROOT = data, cfg_root
    try:
        return fn()
    finally:
        config.DATA_DIR, config.CONFIG_ROOT = real_data, real_root


def test_export_requires_a_token():
    assert client.get("/export").status_code == 403
    assert client.get("/export", headers={"Authorization": "Bearer nope"}).status_code == 403


def test_export_includes_what_cannot_be_regenerated():
    names = _with_fixture(lambda: _export_zip().namelist())
    assert "settings.json" in names
    assert "RESTORE.txt" in names
    joined = "\n".join(names)
    assert "ui_prefs.json" in joined
    assert "trial_start.txt" in joined
    assert "adam.db" in joined
    assert "finance.db" in joined


def test_export_excludes_secrets_and_regenerable_bulk():
    zf = _with_fixture(_export_zip)
    joined = "\n".join(zf.namelist())
    for forbidden in (".env", ".pem", "voice_server.log", "logs/", "backups/",
                      "uploads/", "agent_workspace/", "baseline"):
        assert forbidden not in joined, f"{forbidden} was exported: {joined}"
    # And the key material is not hiding inside any archived member either.
    blob = b"".join(zf.read(n) for n in zf.namelist())
    assert b"sk-ant-should-never-ship" not in blob
    assert b"BEGIN EC PRIVATE KEY" not in blob


def test_sqlite_sidecars_are_never_exported():
    """A -wal/-shm from the LIVE database next to a backup()-produced snapshot is
    worse than useless: on restore SQLite may replay or roll back against it and
    lose the data. Only reproduced against a real running server, because a
    cleanly-closed test database leaves no WAL behind."""
    real_data, real_root = config.DATA_DIR, config.CONFIG_ROOT
    data = _fixture_data_dir()
    # Force a database into WAL mode and leave it open, so the sidecars exist.
    live = data / "state" / "live.db"
    con = sqlite3.connect(live)
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("CREATE TABLE t (a INTEGER)")
    con.execute("INSERT INTO t VALUES (1)")
    con.commit()
    config.DATA_DIR = data
    config.CONFIG_ROOT = Path(tempfile.mkdtemp(prefix="jvl_export_wal_"))
    try:
        assert (data / "state" / "live.db-wal").exists(), "fixture did not create a WAL"
        names = _export_zip().namelist()
        assert not [n for n in names if n.endswith(("-wal", "-shm", "-journal"))], names
        assert any(n.endswith("live.db") for n in names)
    finally:
        con.close()
        config.DATA_DIR, config.CONFIG_ROOT = real_data, real_root


def test_exported_database_is_a_valid_readable_database():
    """The point of using sqlite3.backup() instead of copying the file."""
    zf = _with_fixture(_export_zip)
    member = next(n for n in zf.namelist() if n.endswith("adam.db"))
    out = Path(tempfile.mkdtemp(prefix="jvl_export_check_")) / "adam.db"
    out.write_bytes(zf.read(member))
    con = sqlite3.connect(out)
    try:
        assert con.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert con.execute("SELECT status FROM jobs WHERE id='j1'").fetchone()[0] == "complete"
    finally:
        con.close()


def test_restore_instructions_are_included_and_mention_the_env_gap():
    zf = _with_fixture(_export_zip)
    readme = zf.read("RESTORE.txt").decode("utf-8")
    assert "TO RESTORE" in readme
    assert ".env" in readme          # the one thing they must re-enter
    assert "vault" in readme.lower()  # and the one thing they back up elsewhere


def test_a_corrupt_database_is_skipped_not_fatal():
    real_data, real_root = config.DATA_DIR, config.CONFIG_ROOT
    data = _fixture_data_dir()
    (data / "state" / "broken.db").write_bytes(b"this is not a database at all")
    config.DATA_DIR = data
    config.CONFIG_ROOT = Path(tempfile.mkdtemp(prefix="jvl_export_root2_"))
    try:
        r = client.get("/export", headers=AUTH)
        assert r.status_code == 200, r.text
        names = zipfile.ZipFile(io.BytesIO(r.content)).namelist()
        assert any(n.endswith("adam.db") for n in names)   # the good one still made it
    finally:
        config.DATA_DIR, config.CONFIG_ROOT = real_data, real_root
