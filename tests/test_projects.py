"""Project folders: the store's sync contract and the per-turn project note."""

import session_store
import server


def _sess(key, updated, **kw):
    rec = {"key": key, "title": "t-" + key, "updated": updated}
    rec.update(kw)
    return rec


def test_project_lww_tombstone_and_delivery(tmp_path):
    session_store.init(tmp_path / "s.db")
    assert session_store.upsert_projects([{"key": "p1", "name": "Home", "updated": 10}])["applied"] == 1
    # Older write loses; newer wins.
    assert session_store.upsert_projects([{"key": "p1", "name": "Old", "updated": 5}])["applied"] == 0
    session_store.upsert_projects([{"key": "p1", "name": "House", "instructions": "Be brief.", "icon": "home", "updated": 20}])
    got = session_store.get_project("p1")
    assert got["name"] == "House" and got["instructions"] == "Be brief." and got["icon"] == "home"
    # Delivery cursor: a pull from the last seq sees nothing new.
    rows = session_store.projects_changed_since(0)
    assert [r["key"] for r in rows] == ["p1"]
    assert session_store.projects_changed_since(rows[-1]["seq"]) == []
    # Tombstone hides it from get_project but still delivers.
    session_store.upsert_projects([{"key": "p1", "name": "House", "deleted": True, "updated": 30}])
    assert session_store.get_project("p1") is None
    assert session_store.projects_changed_since(rows[-1]["seq"])[0]["deleted"] is True


def test_session_project_field_survives_old_clients(tmp_path):
    session_store.init(tmp_path / "s.db")
    session_store.upsert([_sess("c1", 10, project="p1")])
    assert session_store.all_sessions()[0]["project"] == "p1"
    # A pre-projects client sends no field (None): the filing is kept.
    session_store.upsert([_sess("c1", 20, project=None)])
    assert session_store.all_sessions()[0]["project"] == "p1"
    # An explicit "" un-files it.
    session_store.upsert([_sess("c1", 30, project="")])
    assert session_store.all_sessions()[0]["project"] == ""


def test_project_note(tmp_path, monkeypatch):
    session_store.init(tmp_path / "s.db")
    monkeypatch.setattr(server, "session_store", session_store)
    session_store.upsert_projects([{"key": "p1", "name": "Home", "instructions": "Cite the lender.", "updated": 1}])
    session_store.upsert([_sess("c1", 1, project="p1", sid="s1", title="Rates"),
                          _sess("c2", 1, project="p1", sid="s2", title="Inspection")])
    note = server._project_note("p1", "s1")
    assert '"Home"' in note and "Cite the lender." in note
    assert "Inspection" in note and "Rates" not in note   # own chat excluded
    assert server._project_note(None) == ""
    assert server._project_note("missing") == ""


def test_last_result_carries_the_originating_chat(tmp_path, monkeypatch):
    """/push/last must name the chat a reply came from, so a device surfacing it
    routes there instead of minting a loose "srv-" duplicate."""
    import json
    monkeypatch.setattr(server, "LAST_RESULT_FILE", tmp_path / "last.json")
    server._store_last_result("hi", "sid-1", 123, prompt="q", chat_key="house-1")
    assert json.loads((tmp_path / "last.json").read_text("utf-8"))["chat_key"] == "house-1"
    server._store_last_result("sms", "", 124)          # server-initiated: no chat
    assert json.loads((tmp_path / "last.json").read_text("utf-8"))["chat_key"] == ""
