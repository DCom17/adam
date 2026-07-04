# Linking Rules

Rules governing the [[Obsidian Vault|JARVIS vault]] knowledge graph auto-linker.

Related: `canonical_nodes.md`, `aliases.md`, `scripts/graph_linker.py`

---

## What Gets Linked

The linker adds Obsidian `[[wiki links]]` to markdown files when it finds approved phrases.

Linkable phrases come from two sources:

1. **Canonical nodes** — the exact node name as listed in `canonical_nodes.md` under `## Auto-Link Nodes`
2. **Approved aliases** — alternate names listed in `aliases.md` that are NOT marked "manual only"

An alias link renders as `[[Canonical Node|alias text]]` in Obsidian, so the display text is preserved.

### Link behavior

- One link per phrase per paragraph (blank line = paragraph boundary)
- Longest phrases matched first to prevent partial matches
- Case-sensitive matching — aliases handle alternate capitalizations explicitly
- The canonical name itself always takes priority over alias entries

---

## What Does NOT Get Linked

The linker never touches:

- **Fenced code blocks** (```` ``` ```` or `~~~`)
- **Inline code** (single backtick `` ` ``)
- **Existing Obsidian links** (`[[...]]`)
- **Markdown links** (`[text](url)`)
- **URLs** (`http://` or `https://`)
- **Heading lines** (lines starting with `#`)
- **Table separator rows**

The linker also skips:

- Files with these extensions: `.json`, `.csv`, `.ps1`, `.js`, `.html`, `.css`, `.xml`, `.yaml`, `.yml`
- Any phrase that appears in the BLOCKED_TOKENS list in `graph_linker.py` (common generic words)
- Aliases marked "manual only" in `aliases.md`
- Nodes listed under `## Manual-Only Nodes` in `canonical_nodes.md`

---

## Safe Folders (auto-link allowed)

| Folder | Contents |
|---|---|
| `01_identity/` | Identity files, user profile, operating rules |
| `02_command_memory/` | Long-term memory, preferences, active context, decisions |
| `04_projects/` | Active project files |
| `05_tasks/` | Active tasks, waiting-on, completed log |
| `06_calendar/` | Calendar packets, workflows, commit logs |
| `07_reviews/` | Daily shutdown and [[Weekly Review|weekly review]] workflows and logs |
| `09_llm_wiki/` | LLM Wiki — concepts, systems, MOCs, etc. |
| `10_graph_schema/` | Graph schema files (canonical nodes, aliases, this file) |
| `11_dashboard/` | [[Hunter Tracker]] markdown files |

---

## Excluded Folders (never auto-link)

| Folder | Reason |
|---|---|
| `00_inbox/` | Raw phone captures — messy, temporary, unverified |
| `03_daily_logs/` | Daily logs — raw working notes, high churn, noisy if linked |
| `08_sources/` | Raw articles, transcripts, course notes — source material, not compiled knowledge |

### Why daily logs are excluded

Daily logs are raw operational notes written under time pressure. They contain fragments, quick thoughts, task lists, and in-progress work. Auto-linking them would:
- Create noisy backlinks from low-quality text
- Make the graph hard to navigate
- Cause link churn as daily logs are frequently edited

Weekly reviews and project files — which are more durable and curated — ARE included. Linking happens at the synthesis layer, not the capture layer.

---

## How to Run the Linker

### Dry run (scan only, no files changed)

```
python scripts/graph_linker.py --dry-run
```

Output:
- Prints candidates found and top linked nodes to console
- Writes full candidate report to `10_graph_schema/link_candidates.md`
- Does NOT modify any vault file

### Apply (add links to files)

```
python scripts/graph_linker.py --apply
```

Output:
- Creates a timestamped backup in `10_graph_schema/backups/`
- Applies approved links to safe .md files
- Updates `10_graph_schema/link_candidates.md` with results

**Always run dry-run first. Review the report before applying.**

---

## How to Add a New Canonical Node

1. Decide if the node should be **auto-linked** or **manual-only**.
   - Auto-link: specific multi-word term that unambiguously refers to one concept
   - Manual-only: single common word or generic concept that could match unrelated text
2. Add the node to the correct section in `canonical_nodes.md`.
3. If the node has common alternate names, add rows to `aliases.md`.
4. Run a dry-run and review `link_candidates.md` before applying.

---

## How to Add an Alias

1. Open `aliases.md`.
2. Add a row: `| alias text | [[Canonical Node]] | notes |`
3. If the alias is a common word or ambiguous phrase, add "manual only" in the Notes column.
4. Run a dry-run to verify behavior.

---

## How to Avoid Graph Noise

- Do not link every occurrence of a word — the one-per-paragraph rule handles this.
- Do not add short or common words as auto-link aliases ("work", "task", "log", "plan", etc.).
- Do not add nodes for temporary or one-off concepts. Nodes should be durable.
- If a term appears in fewer than 3 files, it may not need to be a canonical node yet.
- The graph should feel like a connected knowledge index, not a hyperlink explosion.

---

## Backup and Recovery

Before applying links, the script creates:

```
10_graph_schema/backups/linking_backup_YYYY-MM-DD_HHMMSS/
```

This backup contains a copy of all safe .md files at their pre-link state.

To restore a file from backup:
1. Find the backup folder with the closest timestamp.
2. Copy the file from the backup into the vault.

The linker does not auto-delete backups. Clean them up manually after confirming the apply was successful.
