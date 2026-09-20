"""Native pytest coverage for the Checklists view — store, API, and the
assistant's ACTION path.

The property that matters most here is the delete asymmetry, because it is what
makes it safe to let Adam remove a list it created:

  * DELETE /checklists/{id} ARCHIVES — the row survives, items survive, and
    restore brings it back intact;
  * purge is the only destructive path, it refuses to run on a list that is not
    already archived, and it is absent from the external-actions registry
    entirely, so the assistant cannot reach it at any risk level.

Also pinned: token gating on every route, provenance ('adam' vs 'user'),
progress counts, and the item lifecycle.
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

os.environ.setdefault("ADAM_CONFIG_ROOT", tempfile.mkdtemp(prefix="jvl_ckl_cfg_"))

import config  # noqa: E402

if not config.ADAM_TOKEN:
    config.ADAM_TOKEN = "checklist-test-token-" + "c" * 32
if not config.CLAUDE_EXE:
    config.CLAUDE_EXE = sys.executable

import checklist_store as cs  # noqa: E402
import external_actions  # noqa: E402
import server  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

client = TestClient(server.app)
AUTH = {"Authorization": f"Bearer {config.ADAM_TOKEN}"}


@pytest.fixture(autouse=True)
def fresh_db(tmp_path, monkeypatch):
    """Every test gets its own database, so ordering never matters."""
    db = tmp_path / "checklists.db"
    monkeypatch.setattr(config, "CHECKLIST_DB", db, raising=False)
    cs.close()
    cs.init(db)
    yield
    cs.close()


# --- store ------------------------------------------------------------------

def test_create_with_items_and_progress_counts():
    cid = cs.create_checklist("Move day", description="before the 1st",
                              items=[{"text": "book truck"}, {"text": "pack kitchen"}])
    row = cs.get_checklist(cid)
    assert row["title"] == "Move day"
    assert row["total"] == 2 and row["done"] == 0
    assert [i["text"] for i in row["items"]] == ["book truck", "pack kitchen"]

    cs.toggle_item(row["items"][0]["id"])
    assert cs.get_checklist(cid)["done"] == 1

    listed = cs.list_checklists()
    assert listed[0]["total"] == 2 and listed[0]["done"] == 1


def test_source_is_recorded_and_coerced():
    a = cs.create_checklist("From Adam", source=cs.SOURCE_ADAM)
    u = cs.create_checklist("From me", source="nonsense")
    assert cs.get_checklist(a)["source"] == "adam"
    assert cs.get_checklist(u)["source"] == "user"


def test_archive_is_reversible_and_lossless():
    cid = cs.create_checklist("Errands", items=[{"text": "bank"}, {"text": "post office"}])
    cs.toggle_item(cs.get_checklist(cid)["items"][0]["id"])

    assert cs.archive_checklist(cid) is True
    assert [c["id"] for c in cs.list_checklists()] == []
    assert [c["id"] for c in cs.list_checklists(archived=True)] == [cid]
    # The row and its items are untouched by archiving.
    row = cs.get_checklist(cid)
    assert row is not None and row["total"] == 2 and row["done"] == 1
    assert row["archived_at"]

    assert cs.restore_checklist(cid) is True
    restored = cs.get_checklist(cid)
    assert restored["archived_at"] is None
    assert restored["total"] == 2 and restored["done"] == 1


def test_purge_destroys_and_cascades():
    cid = cs.create_checklist("Temp", items=[{"text": "x"}])
    cs.archive_checklist(cid)
    assert cs.purge_checklist(cid) is True
    assert cs.get_checklist(cid) is None


def test_item_lifecycle_and_bulk_done():
    cid = cs.create_checklist("Steps")
    i1 = cs.add_item(cid, "one")
    i2 = cs.add_item(cid, "two", note="with a note")
    assert cs.get_checklist(cid)["items"][1]["note"] == "with a note"

    assert cs.toggle_item(i1) == {"id": i1, "done": True}
    assert cs.toggle_item(i1) == {"id": i1, "done": False}

    assert cs.update_item(i2, text="two (edited)") is True
    assert cs.get_checklist(cid)["items"][1]["text"] == "two (edited)"

    assert cs.set_all_done(cid, True) == 2
    assert cs.get_checklist(cid)["done"] == 2

    assert cs.delete_item(i1) is True
    assert cs.get_checklist(cid)["total"] == 1
    assert cs.delete_item(i1) is False  # already gone


def test_add_item_to_missing_list_returns_none():
    assert cs.add_item(999999, "orphan") is None


def test_bounds_are_enforced():
    cid = cs.create_checklist("x" * 500)
    assert len(cs.get_checklist(cid)["title"]) <= cs.MAX_TITLE
    iid = cs.add_item(cid, "y" * (cs.MAX_TEXT + 200))
    assert len(cs.get_checklist(cid)["items"][0]["text"]) <= cs.MAX_TEXT
    assert iid is not None


def test_reorder_items():
    cid = cs.create_checklist("Order", items=[{"text": "a"}, {"text": "b"}, {"text": "c"}])
    ids = [i["id"] for i in cs.get_checklist(cid)["items"]]
    cs.reorder_items(cid, [ids[2], ids[0], ids[1]])
    assert [i["text"] for i in cs.get_checklist(cid)["items"]] == ["c", "a", "b"]


def test_summary_counts_only_active():
    a = cs.create_checklist("live", items=[{"text": "1"}, {"text": "2"}])
    b = cs.create_checklist("gone", items=[{"text": "3"}])
    cs.toggle_item(cs.get_checklist(a)["items"][0]["id"])
    cs.archive_checklist(b)
    s = cs.summary()
    assert s == {"active": 1, "archived": 1, "items": 2, "items_done": 1}


# --- API --------------------------------------------------------------------

def test_every_route_is_token_gated():
    cid = cs.create_checklist("Gated")
    iid = cs.add_item(cid, "step")
    for method, path in [
        ("get", "/checklists"),
        ("post", "/checklists"),
        ("get", f"/checklists/{cid}"),
        ("patch", f"/checklists/{cid}"),
        ("delete", f"/checklists/{cid}"),
        ("post", f"/checklists/{cid}/restore"),
        ("post", f"/checklists/{cid}/purge"),
        ("post", "/checklists/reorder"),
        ("post", f"/checklists/{cid}/items"),
        ("patch", f"/checklists/items/{iid}"),
        ("post", f"/checklists/items/{iid}/toggle"),
        ("delete", f"/checklists/items/{iid}"),
        ("post", f"/checklists/{cid}/done"),
    ]:
        # get/delete take no body in this client version; post/patch do.
        kw = {"json": {}} if method in ("post", "patch") else {}
        r = getattr(client, method)(path, **kw)
        assert r.status_code in (401, 403), f"{method.upper()} {path} was not gated ({r.status_code})"


def test_api_create_read_and_archive_flow():
    r = client.post("/checklists", headers=AUTH, json={
        "title": "Lender calls", "description": "before the 20th",
        "items": [{"text": "Call 1st Tribal"}, {"text": "Get insurance quote"}],
    })
    assert r.status_code == 200, r.text
    cid = r.json()["checklist"]["id"]
    assert r.json()["checklist"]["total"] == 2

    assert client.get("/checklists", headers=AUTH).json()["summary"]["active"] == 1

    # DELETE archives rather than destroying.
    assert client.delete(f"/checklists/{cid}", headers=AUTH).status_code == 200
    assert client.get("/checklists", headers=AUTH).json()["checklists"] == []
    arch = client.get("/checklists?archived=true", headers=AUTH).json()["checklists"]
    assert [c["id"] for c in arch] == [cid]

    assert client.post(f"/checklists/{cid}/restore", headers=AUTH).status_code == 200
    assert client.get(f"/checklists/{cid}", headers=AUTH).json()["total"] == 2


def test_purge_refuses_an_active_list():
    cid = cs.create_checklist("Still active")
    r = client.post(f"/checklists/{cid}/purge", headers=AUTH)
    assert r.status_code == 409
    assert cs.get_checklist(cid) is not None

    cs.archive_checklist(cid)
    assert client.post(f"/checklists/{cid}/purge", headers=AUTH).status_code == 200
    assert cs.get_checklist(cid) is None


def test_api_rejects_empty_title_and_unknown_ids():
    assert client.post("/checklists", headers=AUTH, json={"title": "   "}).status_code == 400
    assert client.get("/checklists/424242", headers=AUTH).status_code == 404
    assert client.delete("/checklists/424242", headers=AUTH).status_code == 404
    assert client.post("/checklists/items/424242/toggle", headers=AUTH).status_code == 404


def test_api_rejects_unknown_fields():
    """StrictModel: extra='forbid' keeps the request surface pinned."""
    r = client.post("/checklists", headers=AUTH,
                    json={"title": "ok", "sneaky": "value"})
    assert r.status_code == 422


def test_api_item_add_and_toggle():
    cid = cs.create_checklist("Steps")
    r = client.post(f"/checklists/{cid}/items", headers=AUTH, json={"text": "first"})
    assert r.status_code == 200
    iid = r.json()["item_id"]
    assert client.post(f"/checklists/items/{iid}/toggle", headers=AUTH).json()["done"] is True
    assert client.get(f"/checklists/{cid}", headers=AUTH).json()["done"] == 1
    assert client.post(f"/checklists/{cid}/items", headers=AUTH,
                       json={"text": "  "}).status_code == 400


def test_checklists_view_page_is_served():
    r = client.get("/checklists-view")
    assert r.status_code == 200
    assert "Checklists" in r.text


# --- the assistant's path ---------------------------------------------------

def test_action_registry_exposes_create_and_archive_but_never_purge():
    types = external_actions.known_types()
    assert "checklist.create" in types
    assert "checklist.add_items" in types
    assert "checklist.archive" in types
    # The destructive route is deliberately unreachable from a brain block.
    assert not any("purge" in t for t in types)
    for t in ("checklist.create", "checklist.add_items", "checklist.archive"):
        assert external_actions.brain_proposable(t) is True


def test_action_create_marks_provenance_adam():
    res = external_actions.execute("checklist.create", {
        "title": "From a conversation",
        "items": ["call the lender", {"text": "get a quote", "note": "exact address"}],
    })
    row = cs.get_checklist(res["checklist_id"])
    assert row["source"] == "adam"
    assert [i["text"] for i in row["items"]] == ["call the lender", "get a quote"]
    assert row["items"][1]["note"] == "exact address"


def test_action_add_items_and_archive():
    cid = cs.create_checklist("Target", source=cs.SOURCE_ADAM)
    res = external_actions.execute("checklist.add_items",
                                   {"checklist_id": cid, "items": ["a", "b", "  "]})
    assert res["added"] == 2

    res = external_actions.execute("checklist.archive", {"checklist_id": cid})
    assert res["recoverable"] is True
    assert cs.get_checklist(cid)["archived_at"]     # archived, not destroyed
    assert cs.restore_checklist(cid) is True        # and the user can undo it


def test_action_payloads_are_validated():
    for atype, payload in [
        ("checklist.create", {"title": "   "}),
        ("checklist.create", {"title": "ok", "items": "not-a-list"}),
        ("checklist.add_items", {"items": ["x"]}),
        ("checklist.add_items", {"checklist_id": 1, "items": []}),
        ("checklist.add_items", {"checklist_id": 999999, "items": ["x"]}),
        ("checklist.archive", {"checklist_id": "three"}),
        ("checklist.archive", {"checklist_id": 999999}),
    ]:
        with pytest.raises(external_actions.ActionError):
            external_actions.execute(atype, payload)


def test_prompt_note_tells_the_agent_checklists_auto_run():
    note = server._action_proposal_note()
    assert "checklist.create" in note
    assert "RUN IMMEDIATELY" in note
    # And it must not tell the agent to wait for approval on these.
    assert "Apart from checklist.*" in note
