"""FastAPI routers, split out of the old server.py monolith.

Each module holds one feature area's routes. Shared mutable state (the Claude
driver, live-turn registry, push helpers, ui-prefs) stays defined in server.py;
routers reach it as ``server.<name>`` at request time so tests (and future
code) can monkeypatch ``server`` attributes and every route sees the patch.
"""
