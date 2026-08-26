"""Source guards for the DOM-XSS class fixed in the trackers.

The web/ pages are hand-written, no build step, so there is no linter between an
edit and a shipped release. These are the two invariants worth pinning:

1. Every escaper escapes all five characters. Four of the five used to stop at
   &<>" (one at &<>), which is fine right up until a call site writes a
   single-quoted attribute — and the reviewer has no way to see the difference.

2. The .replace(/'/g, "\\'") idiom never comes back. It was an attempt to escape
   for a JS string sitting inside an HTML attribute, which cannot work: entities
   decode before the JS is parsed, and a backslash in the data turns \\' into an
   escaped backslash followed by a live quote. finance.html reached that sink
   with txn_key, account name and batch_id — all of which come from an imported
   statement file. Values now travel via data- attributes instead.

Reachability that made this worth fixing rather than noting: make_txn_key builds
its key from the statement's own date and account fields, and _iso_date passes an
unparseable date through unchanged.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

WEB = Path(__file__).resolve().parent.parent / "web"

# Files that define an HTML escaper and build markup by string concatenation.
ESCAPER_FILES = [
    "index.html",
    "finance.html",
    "health.html",
    "hunter-dashboard.html",
]

# &  <  >  "  '  — the full set. An escaper missing any one of these is only
# safe by accident of how its callers happen to quote attributes.
REQUIRED_ENTITIES = ["&amp;", "&lt;", "&gt;", "&quot;", "&#39;"]

# The idiom that cannot be made correct in a JS-string-inside-attribute context.
BACKSLASH_QUOTE_HACK = re.compile(r"""replace\(\s*/'/g\s*,\s*["']\\\\?'["']\s*\)""")


def _read(name: str) -> str:
    return (WEB / name).read_text(encoding="utf-8")


@pytest.mark.parametrize("name", ESCAPER_FILES)
def test_escaper_covers_all_five_characters(name: str):
    src = _read(name)
    # Every line that defines an esc/escapeHtml function must name all five
    # replacements. (Several files define more than one escaper.)
    definitions = [
        ln for ln in src.splitlines()
        if re.search(r"(function\s+esc\w*\s*\(|const\s+esc\w*\s*=|escapeHtml\s*[=(])", ln)
        and "replace" in ln
    ]
    assert definitions, f"{name}: found no escaper definition to check"
    for ln in definitions:
        missing = [e for e in REQUIRED_ENTITIES if e not in ln]
        assert not missing, f"{name}: escaper is missing {missing}\n  {ln.strip()}"


@pytest.mark.parametrize("name", sorted(p.name for p in WEB.glob("*.html")))
def test_no_backslash_quote_escaping_hack(name: str):
    src = _read(name)
    hits = [
        f"{i}: {ln.strip()}"
        for i, ln in enumerate(src.splitlines(), 1)
        if BACKSLASH_QUOTE_HACK.search(ln)
    ]
    assert not hits, (
        f"{name}: the .replace(/'/g,\"\\\\'\") idiom is back. It cannot secure a JS "
        f"string inside an HTML attribute — pass the value through a data- "
        f"attribute and read this.dataset instead.\n  " + "\n  ".join(hits)
    )


def test_tracker_row_buttons_use_data_attributes():
    """The specific sinks that were reachable from an imported statement."""
    fin = _read("finance.html")
    for handler, attr in [
        ("delTxn(this,this.dataset.key)", "data-key"),
        ("delAccount(this,this.dataset.name)", "data-name"),
        ("delImport(this,this.dataset.batch)", "data-batch"),
        ("delSnap(this,this.dataset.date)", "data-date"),
        ("showReview(this.dataset.batch)", "data-batch"),
        ("approveBatch(this.dataset.batch)", "data-batch"),
        ("discardBatch(this.dataset.batch)", "data-batch"),
    ]:
        assert handler in fin, f"finance.html no longer wires {handler}"
        assert attr in fin

    health = _read("health.html")
    assert "addWater(Number(this.dataset.amt),this.dataset.unit,this)" in health
