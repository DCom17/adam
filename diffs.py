"""
Adam — diff + hash helpers (Phase 4 Trustworthy Change Review).

Small, dependency-free utilities that let the proposed-change flow answer three
questions a user needs before approving a write:

    * What exactly changes?      -> a readable unified diff
    * What is the file now?       -> a stable SHA-256 of its bytes + its size
    * Did it change underneath?   -> compare a stored hash to the current hash

Everything here is best-effort and never raises into the caller. A file that is
binary, missing, unreadable, or too large does not crash the flow — it produces
a diff marked *unavailable* with a clear reason, while the hash/size are still
captured where possible so staleness detection keeps working.

Hashes are computed over raw bytes (so binary files still get a baseline hash for
conflict detection); diffs are only generated for UTF-8 text within the size cap.
"""

from __future__ import annotations

import difflib
import hashlib
import time
from pathlib import Path

import config

# A unified diff for a huge file is useless to a human and heavy on the wire.
# Cap the rendered diff; the hashes still cover the whole file for conflict checks.
MAX_DIFF_CHARS = 200_000


def _max_bytes() -> int:
    return config.PERM_MAX_FILE_SIZE_MB * 1024 * 1024


def now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime())


def sha256_text(text: str) -> str:
    """SHA-256 of a string's UTF-8 bytes."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def hash_and_size_of_file(path: str | Path) -> tuple[str | None, int | None]:
    """(sha256_hex, size_bytes) of an existing file, or (None, None) if it does
    not exist / can't be read. Computed over raw bytes, so it works for binary
    files too — this is the baseline used for staleness/conflict detection."""
    p = Path(path)
    if not p.is_file():
        return None, None
    try:
        data = p.read_bytes()
    except OSError:
        return None, None
    return hashlib.sha256(data).hexdigest(), len(data)


def _read_text_for_diff(path: str | Path) -> tuple[str | None, str | None]:
    """Read a file as text for diffing.

    Returns (text, unavailable_reason):
      * missing file        -> ("", None)           treated as empty original
      * too large           -> (None, reason)
      * binary (NUL byte)   -> (None, "binary file")
      * not UTF-8           -> (None, reason)
      * unreadable          -> (None, reason)
    """
    p = Path(path)
    if not p.is_file():
        return "", None  # nonexistent baseline — an empty original is fine to diff
    try:
        size = p.stat().st_size
    except OSError as e:
        return None, f"unreadable ({e})"
    cap = _max_bytes()
    if size > cap:
        return None, (
            f"file too large for diff ({size / 1024 / 1024:.1f} MB > "
            f"{config.PERM_MAX_FILE_SIZE_MB} MB)"
        )
    try:
        data = p.read_bytes()
    except OSError as e:
        return None, f"unreadable ({e})"
    if b"\x00" in data:
        return None, "binary file"
    try:
        return data.decode("utf-8"), None
    except UnicodeDecodeError:
        return None, "not valid UTF-8 text"


def _normalize_newlines(text: str) -> str:
    """Collapse CRLF / lone CR to LF for diff display only. This keeps a pure
    content change from showing every line as changed just because the on-disk
    file uses Windows line endings and the proposed content uses \\n. Hashes are
    still computed over the raw bytes, so conflict detection is unaffected."""
    return text.replace("\r\n", "\n").replace("\r", "\n")


def _unified(a_text: str, b_text: str, a_label: str, b_label: str) -> str:
    """A unified diff string between two texts, line-based with 3 lines context.
    Line endings are normalized so the diff reflects content changes, not CRLF
    vs LF noise."""
    a = _normalize_newlines(a_text).splitlines(keepends=True)
    b = _normalize_newlines(b_text).splitlines(keepends=True)
    out = "".join(
        difflib.unified_diff(a, b, fromfile=a_label, tofile=b_label, n=3)
    )
    if len(out) > MAX_DIFF_CHARS:
        out = out[:MAX_DIFF_CHARS] + "\n… [diff truncated]\n"
    return out


def build_diff_fields(
    *,
    target_path: str | Path,
    action: str,
    content: str | None,
    new_path: str | Path | None = None,
) -> dict:
    """Compute the Phase 4 review fields for a proposed change.

    Always returns a dict with every key present (values may be None):
      original_hash, original_size, proposed_hash, proposed_size,
      diff, diff_available, diff_unavailable_reason, diff_created_at.

    `original_*` reflect the target file as it is *now* (the proposal's baseline);
    `proposed_*` reflect the content Claude wants to write. The diff is rendered
    only for readable UTF-8 text within the size cap; otherwise it is marked
    unavailable with a human-readable reason."""
    fields: dict = {
        "original_hash": None,
        "original_size": 0,
        "proposed_hash": None,
        "proposed_size": None,
        "diff": None,
        "diff_available": False,
        "diff_unavailable_reason": None,
        "diff_created_at": now_iso(),
    }

    target = Path(target_path)

    # Baseline hash/size over raw bytes — works for binary files too, and is what
    # staleness detection compares against at apply time.
    oh, osize = hash_and_size_of_file(target)
    fields["original_hash"] = oh
    fields["original_size"] = osize if osize is not None else 0

    # Proposed content hash/size (content actions only).
    if action in ("create", "edit", "replace") and content is not None:
        fields["proposed_hash"] = sha256_text(content)
        fields["proposed_size"] = len(content.encode("utf-8"))

    # --- per-action diff ---
    if action == "rename":
        # Pure metadata move (no content change in the current schema): show the
        # rename as a readable two-line diff. A content change alongside a rename
        # isn't modeled by the schema, so we don't attempt a content diff here.
        old = str(target)
        new = str(new_path) if new_path else "(unspecified)"
        fields["diff"] = f"# rename\n- {old}\n+ {new}\n"
        fields["diff_available"] = True
        return fields

    original_text, reason = _read_text_for_diff(target)
    if original_text is None:
        # Original can't be rendered (binary / too large / unreadable). The hash
        # baseline above still stands for conflict detection; the diff doesn't.
        fields["diff_available"] = False
        fields["diff_unavailable_reason"] = reason or "original not readable as text"
        return fields

    if action == "delete":
        # Deletion diff: everything removed. If the file doesn't exist there's
        # nothing to show.
        if not target.is_file():
            fields["diff_available"] = False
            fields["diff_unavailable_reason"] = "target does not exist"
            return fields
        fields["diff"] = _unified(original_text, "", str(target), "(deleted)")
        fields["diff_available"] = True
        return fields

    # create / edit / replace
    proposed_text = content if content is not None else ""
    label_from = str(target) if target.is_file() else "(new file)"
    fields["diff"] = _unified(original_text, proposed_text, label_from, str(target))
    fields["diff_available"] = True
    return fields
