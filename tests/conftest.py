"""Suite-wide safety: no test may ever deliver a real push notification.

A temp ADAM_CONFIG_ROOT without its own settings.json still resolves STATE_DIR
to this install's real data/state, so a test process holds the owner's real
push subscriptions and VAPID key. ADAM_NO_PUSH makes server._deliver_push stop
before the real push service (a test's own fake sender still runs) and keeps
the reminder loop from starting. Set here, before any test module imports
server; the legacy suite's subprocesses inherit it through the environment.
"""

import os

os.environ["ADAM_NO_PUSH"] = "1"

# Same hazard for the job store: without this every test that boots the app opens the
# owner's REAL data/state/adam.db, and startup recovery there once flipped two live phone
# turns to "interrupted" mid-reply (2026-10-07). One temp DB per run, inherited by the
# legacy suite's subprocesses.
if not os.environ.get("ADAM_JOBS_DB"):
    import tempfile
    os.environ["ADAM_JOBS_DB"] = os.path.join(tempfile.mkdtemp(prefix="adam_test_jobs_"), "adam.db")
# …and the synced-chat store: a finished test turn can now write its reply into a chat.
if not os.environ.get("ADAM_SESSIONS_DB"):
    import tempfile
    os.environ["ADAM_SESSIONS_DB"] = os.path.join(tempfile.mkdtemp(prefix="adam_test_sessions_"), "sessions.db")
