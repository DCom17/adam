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
