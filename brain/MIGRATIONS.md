# Migrations

When a new kit version needs a one-time change to **existing user data** (not just
new framework files), record it here. "Update Adam" reads this after copying
core files and applies any migration whose version is newer than the user's
previous VERSION.

A migration is only needed when the *shape* of user data changes — e.g. a new
required field in `dashboard_state.json`, a renamed CSV column, a moved file.
Pure framework changes (rules, workflows, scripts) need no migration; the manifest
copy handles them.

## Format

```
## <version>  (e.g. 0.2.0)
- <what changed in user-data shape>
- Migration: <exact steps Adam should take on the user's files>
- Safe/idempotent: <yes/no — can it run twice without harm?>
```

## History

_(none yet — 0.1.0 is the initial release)_
