"""
Checklists routes — the JSON API behind the Checklists view.

Named lists of ordered, checkable steps. Two authors write here: the user, from
the Checklists page, and Adam, through `checklist.*` ACTION blocks (which the
server executes — the agent never touches this DB directly). Both land in the
same store, distinguished only by the `source` column so the UI can badge what
the assistant made.

The delete story is the important one: DELETE /checklists/{id} **archives**. The
list moves to the Archive tab and stays restorable. Permanent removal is a
separate, explicit route (`/checklists/{id}/purge`) that the UI reaches only from
the archive and that Adam cannot propose at all (see external_actions). That
asymmetry is what makes it safe to let the assistant delete lists it created.

All routes are token-gated, same as /finance/* and /health/*.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

import checklist_store as cs
from models import (
    ChecklistBulkDoneBody,
    ChecklistCreateBody,
    ChecklistEditBody,
    ChecklistItemBody,
    ChecklistItemEdit,
    ChecklistReorderBody,
)
from security import require_token

router = APIRouter()


# --- Lists ------------------------------------------------------------------

@router.get("/checklists", dependencies=[Depends(require_token)])
async def checklists_index(archived: bool = False):
    """The active lists, or the archive when `archived=true`. Each row carries a
    done/total count so the index renders in one request."""
    return {"checklists": cs.list_checklists(archived=archived), "summary": cs.summary()}


@router.post("/checklists", dependencies=[Depends(require_token)])
async def checklists_create(body: ChecklistCreateBody):
    """Create a checklist, optionally with all of its steps in one call."""
    if not (body.title or "").strip():
        raise HTTPException(status_code=400, detail="A checklist needs a title.")
    cid = cs.create_checklist(
        title=body.title,
        description=body.description,
        source=body.source,
        items=[i.model_dump() for i in body.items],
    )
    return {"ok": True, "checklist": cs.get_checklist(cid)}


@router.get("/checklists/{checklist_id}", dependencies=[Depends(require_token)])
async def checklists_get(checklist_id: int):
    item = cs.get_checklist(checklist_id)
    if item is None:
        raise HTTPException(status_code=404, detail="No such checklist.")
    return item


@router.patch("/checklists/{checklist_id}", dependencies=[Depends(require_token)])
async def checklists_edit(checklist_id: int, body: ChecklistEditBody):
    if not cs.update_checklist(checklist_id, title=body.title, description=body.description):
        raise HTTPException(status_code=404, detail="No such checklist, or nothing to change.")
    return {"ok": True, "checklist": cs.get_checklist(checklist_id)}


@router.delete("/checklists/{checklist_id}", dependencies=[Depends(require_token)])
async def checklists_archive(checklist_id: int):
    """Archive (not destroy). Restorable from the Archive tab."""
    if not cs.archive_checklist(checklist_id):
        raise HTTPException(status_code=404, detail="No such active checklist.")
    return {"ok": True, "archived": checklist_id}


@router.post("/checklists/{checklist_id}/restore", dependencies=[Depends(require_token)])
async def checklists_restore(checklist_id: int):
    if not cs.restore_checklist(checklist_id):
        raise HTTPException(status_code=404, detail="No such archived checklist.")
    return {"ok": True, "checklist": cs.get_checklist(checklist_id)}


@router.post("/checklists/{checklist_id}/purge", dependencies=[Depends(require_token)])
async def checklists_purge(checklist_id: int):
    """Permanent delete. Only offered from the Archive tab, and never proposable
    by the assistant — a list must be archived first, which the user can see."""
    row = cs.get_checklist(checklist_id)
    if row is None:
        raise HTTPException(status_code=404, detail="No such checklist.")
    if not row.get("archived_at"):
        raise HTTPException(
            status_code=409,
            detail="Archive the checklist before deleting it permanently.",
        )
    cs.purge_checklist(checklist_id)
    return {"ok": True, "purged": checklist_id}


@router.post("/checklists/reorder", dependencies=[Depends(require_token)])
async def checklists_reorder(body: ChecklistReorderBody):
    return {"ok": True, "moved": cs.reorder_checklists(body.order)}


# --- Items ------------------------------------------------------------------

@router.post("/checklists/{checklist_id}/items", dependencies=[Depends(require_token)])
async def checklist_item_add(checklist_id: int, body: ChecklistItemBody):
    if not (body.text or "").strip():
        raise HTTPException(status_code=400, detail="A step needs some text.")
    iid = cs.add_item(checklist_id, body.text, note=body.note, done=body.done)
    if iid is None:
        raise HTTPException(
            status_code=404,
            detail="No such checklist, or it already has the maximum number of steps.",
        )
    return {"ok": True, "item_id": iid, "checklist": cs.get_checklist(checklist_id)}


@router.patch("/checklists/items/{item_id}", dependencies=[Depends(require_token)])
async def checklist_item_edit(item_id: int, body: ChecklistItemEdit):
    if not cs.update_item(item_id, text=body.text, note=body.note, done=body.done):
        raise HTTPException(status_code=404, detail="No such step, or nothing to change.")
    return {"ok": True}


@router.post("/checklists/items/{item_id}/toggle", dependencies=[Depends(require_token)])
async def checklist_item_toggle(item_id: int):
    state = cs.toggle_item(item_id)
    if state is None:
        raise HTTPException(status_code=404, detail="No such step.")
    return {"ok": True, **state}


@router.delete("/checklists/items/{item_id}", dependencies=[Depends(require_token)])
async def checklist_item_delete(item_id: int):
    if not cs.delete_item(item_id):
        raise HTTPException(status_code=404, detail="No such step.")
    return {"ok": True, "deleted": item_id}


@router.post("/checklists/{checklist_id}/items/reorder", dependencies=[Depends(require_token)])
async def checklist_items_reorder(checklist_id: int, body: ChecklistReorderBody):
    return {"ok": True, "moved": cs.reorder_items(checklist_id, body.order)}


@router.post("/checklists/{checklist_id}/done", dependencies=[Depends(require_token)])
async def checklist_bulk_done(checklist_id: int, body: ChecklistBulkDoneBody):
    """Check or clear every step at once."""
    if cs.get_checklist(checklist_id) is None:
        raise HTTPException(status_code=404, detail="No such checklist.")
    return {"ok": True, "changed": cs.set_all_done(checklist_id, body.done)}
