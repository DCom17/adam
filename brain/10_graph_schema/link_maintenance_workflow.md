# Link Maintenance Workflow

> **Advanced / not available in the standard safe build.** The linker is a Python script;
> the assistant has no shell in safe mode, so the `python scripts/graph_linker.py` steps
> below don't run here. Hand-created `[[wiki links]]` still follow the schema rules; the
> automated dry-run/apply commands are declined (see CLAUDE.md → Knowledge Graph Linking).

Standard operating procedure for maintaining the [[JARVIS Command System|JARVIS]] knowledge graph linking system.

Related: `linking_rules.md`, `canonical_nodes.md`, `aliases.md`, `scripts/graph_linker.py`

---

## When to Run the Linker

Run the linker when:
- A significant batch of new content has been added to safe folders (projects, wiki, tasks, calendar)
- A new canonical node has been added and you want to back-fill links in existing files
- Weekly review identifies a recurring concept that should be a canonical node

Do NOT run the linker:
- On a daily basis (too much churn for little gain)
- Immediately after writing raw planning notes (let them stabilize first)
- If the dry-run report shows suspicious or noisy candidates

---

## Standard Workflow

### Step 1 — Review the schema

Before running, check:

- [ ] `canonical_nodes.md` — is the node list accurate and current?
- [ ] `aliases.md` — are all aliases conservative and unambiguous?
- [ ] No new generic words have been accidentally added as auto-link aliases

### Step 2 — Run dry-run

```
python scripts/graph_linker.py --dry-run
```

Review `10_graph_schema/link_candidates.md`:

- Are the top linked nodes the expected high-value concepts?
- Are there any obviously wrong candidates (generic words linked out of context)?
- Is the total candidate count reasonable? (Tens to hundreds is fine. Thousands suggests an overly broad alias.)

### Step 3 — Evaluate candidates

For each top node, ask:
- Does this link add context that helps navigate the vault?
- Would a reader benefit from following this link?
- Is the phrase clearly referring to this concept in context?

If yes → proceed to apply.
If doubtful → refine aliases or add the phrase to BLOCKED_TOKENS in the script.

### Step 4 — Apply links

Only after dry-run looks clean:

```
python scripts/graph_linker.py --apply
```

The script creates a timestamped backup before making any changes.

### Step 5 — Verify results

After applying:
- Open a few changed files in Obsidian and verify links look correct
- Check the Obsidian graph view for the newly linked nodes
- Confirm no unexpected links appeared in identity or memory files

### Step 6 — Log the run

Add a note to today's daily log or `02_command_memory/active_context.md`:
- Date of run
- Files changed
- Links applied
- Any issues found

---

## Adding a New Canonical Node

1. Identify a concept that appears in 3+ files and represents a durable, meaningful idea.
2. Choose a canonical name — the exact `[[link text]]` that will appear in Obsidian.
3. Decide: auto-link or manual-only?
   - Multi-word specific concepts → auto-link
   - Single common English words → manual-only
4. Add to `canonical_nodes.md` under the correct section.
5. Add any aliases to `aliases.md` (with conservative rules — no generic words).
6. Run dry-run and verify.

---

## Removing or Renaming a Node

If a canonical node name changes:
1. Update `canonical_nodes.md` with the new name.
2. Update `aliases.md` — add the old name as an alias if it appears in existing files.
3. Manually update existing `[[OldName]]` links in the vault (use Obsidian's rename function).
4. Run dry-run with the new name to verify coverage.

---

## When NOT to Auto-Link

Concepts that should always be linked manually:
- Stat names: Discipline, Knowledge, Health, Finance, Career, Spiritual, Social, Execution
- People's names (add to `09_llm_wiki/people/` manually)
- Specific course or unit codes
- One-off project names that aren't recurring concepts
- Anything inside daily logs, phone captures, or raw sources

---

## Backup Management

Backups are stored in `10_graph_schema/backups/linking_backup_YYYY-MM-DD_HHMMSS/`.

Keep the last 3 backups. Delete older ones manually after confirming correctness.

Do not commit backup folders to any sync system unless intentional.
