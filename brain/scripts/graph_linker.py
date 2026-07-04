#!/usr/bin/env python3
"""
JARVIS Obsidian Knowledge Graph Linker
Conservative auto-linker for the JARVIS vault.

Usage:
    python graph_linker.py --dry-run    Scan and report candidates. No files modified.
    python graph_linker.py --apply      Apply links after dry-run. Creates backup first.

Safe folders (configured in this script):
    01_identity, 02_command_memory, 04_projects, 05_tasks,
    06_calendar, 07_reviews, 09_llm_wiki, 10_graph_schema, 11_dashboard

Excluded folders:
    00_inbox, 03_daily_logs, 08_sources (raw/messy content)

Excluded extensions:
    .json, .csv, .ps1, .js, .html, .css, .xml, .yaml, .yml
"""

import argparse
import re
import sys
import shutil
from collections import Counter
from datetime import datetime
from pathlib import Path

# ── Configuration ──────────────────────────────────────────────────────────────

VAULT_ROOT = Path(__file__).parent.parent
GRAPH_SCHEMA_DIR = VAULT_ROOT / "10_graph_schema"
CANONICAL_NODES_FILE = GRAPH_SCHEMA_DIR / "canonical_nodes.md"
ALIASES_FILE = GRAPH_SCHEMA_DIR / "aliases.md"
LINK_CANDIDATES_FILE = GRAPH_SCHEMA_DIR / "link_candidates.md"
BACKUPS_DIR = GRAPH_SCHEMA_DIR / "backups"

SAFE_FOLDERS = [
    "01_identity",
    "02_command_memory",
    "04_projects",
    "05_tasks",
    "06_calendar",
    "07_reviews",
    "09_llm_wiki",
    "10_graph_schema",
    "11_dashboard",
]

EXCLUDED_EXTENSIONS = {
    ".json", ".csv", ".ps1", ".js", ".html",
    ".css", ".xml", ".yaml", ".yml",
}

# Single tokens that are too generic to auto-link even if they match
BLOCKED_TOKENS: set[str] = {
    "work", "school", "day", "task", "system", "boss", "level", "rank",
    "quest", "stat", "log", "plan", "review", "report", "track", "link",
    "sync", "build", "phase", "step", "status", "note", "entry", "time",
    "week", "month", "year", "today", "tomorrow", "data", "file", "folder",
    "page", "item", "list", "table", "block", "type", "mode", "state",
    "value", "count", "point", "section", "rule", "term", "word", "text",
    "goal", "action", "event", "check", "run", "set", "get", "use", "add",
    "job", "role", "team", "area", "name", "date", "hour", "base", "core",
}


# ── Parsing ────────────────────────────────────────────────────────────────────

def parse_canonical_nodes(path: Path) -> list[str]:
    """
    Extract auto-link canonical node names from canonical_nodes.md.
    Only nodes under '## Auto-Link' sections are included.
    Nodes under '## Manual-Only' sections are skipped.
    """
    nodes: list[str] = []
    if not path.exists():
        print(f"WARNING: {path} not found.", file=sys.stderr)
        return nodes

    auto_link = False

    for line in path.read_text(encoding="utf-8").splitlines():
        s = line.strip()

        if s.startswith("## "):
            lower = s.lower()
            if "auto" in lower:
                auto_link = True
            elif "manual" in lower:
                auto_link = False
            else:
                auto_link = False
            continue

        # Sub-headings (###) do not change auto_link state
        if s.startswith("#"):
            continue

        if auto_link:
            m = re.match(r"-\s*\[\[(.+?)\]\]", s)
            if m:
                nodes.append(m.group(1).strip())

    return nodes


def parse_aliases(path: Path) -> dict[str, str]:
    """
    Parse aliases.md markdown table.
    Returns {alias_text: canonical_node_name}.
    Skips rows that contain 'manual only' or 'manual-only' anywhere in the row.
    """
    aliases: dict[str, str] = {}
    if not path.exists():
        print(f"WARNING: {path} not found.", file=sys.stderr)
        return aliases

    in_table = False
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()

        if "|" not in line:
            if in_table:
                in_table = False
            continue

        # Detect table header row
        if re.search(r"\|\s*Alias\s*\|", line, re.IGNORECASE):
            in_table = True
            continue

        # Skip separator rows
        if re.match(r"\|[\s\-:|]+\|", line):
            continue

        if in_table:
            parts = [p.strip() for p in line.strip("|").split("|")]
            if len(parts) < 2:
                continue

            alias_text = parts[0].strip()
            canonical_raw = parts[1].strip()

            # Skip if marked manual-only anywhere in the row
            if "manual only" in line.lower() or "manual-only" in line.lower():
                continue

            if not alias_text:
                continue

            # Skip generic tokens
            if alias_text.lower() in BLOCKED_TOKENS:
                continue

            # Extract canonical name from [[...]] if present
            m = re.search(r"\[\[(.+?)\]\]", canonical_raw)
            canonical = m.group(1).strip() if m else canonical_raw.strip()

            if canonical:
                aliases[alias_text] = canonical

    return aliases


def build_link_map(canonical_nodes: list[str], aliases: dict[str, str]) -> dict[str, str]:
    """
    Returns {phrase: canonical_node_name}.
    Canonical nodes map to themselves.
    Aliases map to their canonical target without overriding canonical names.
    """
    link_map: dict[str, str] = {}
    for node in canonical_nodes:
        link_map[node] = node
    for alias, canonical in aliases.items():
        if alias not in link_map:
            link_map[alias] = canonical
    return link_map


# ── File collection ────────────────────────────────────────────────────────────

def get_safe_files(vault_root: Path) -> list[Path]:
    """Return all .md files inside safe folders only."""
    files: list[Path] = []
    for folder_name in SAFE_FOLDERS:
        folder = vault_root / folder_name
        if not folder.exists():
            continue
        for f in folder.rglob("*"):
            if f.is_file() and f.suffix.lower() == ".md":
                files.append(f)
    return sorted(files)


# ── Context detection ──────────────────────────────────────────────────────────

def _in_inline_code(line_text: str, pos: int) -> bool:
    """True if pos is inside backtick inline code on the same line."""
    return line_text[:pos].count("`") % 2 == 1


def _in_existing_link(line_text: str, pos: int) -> bool:
    """True if pos is inside an existing [[link]] or [text](url)."""
    pre = line_text[:pos]
    # Inside unclosed [[
    if "[[" in pre:
        after_open = pre.rsplit("[[", 1)[-1]
        if "]]" not in after_open:
            return True
    # Inside an unclosed markdown link [text](
    if re.search(r"\[[^\]]*$", pre):
        return True
    return False


def _in_url(line_text: str, pos: int) -> bool:
    """True if pos appears to be inside a URL."""
    pre = line_text[:pos]
    return bool(re.search(r"https?://\S*$", pre))


def _is_heading(line: str) -> bool:
    return line.lstrip().startswith("#")


def _is_table_separator(line: str) -> bool:
    return bool(re.match(r"\s*\|?\s*[-:]+[-| :]*\|?\s*$", line.strip()))


# ── Scanning (dry-run phase) ───────────────────────────────────────────────────

def find_candidates_in_file(
    file_path: Path,
    link_map: dict[str, str],
    vault_root: Path,
) -> list[dict]:
    """
    Scan a single file for link candidates without modifying it.
    Returns list of candidate dicts with keys: file, phrase, canonical, line_num, line_text.
    """
    try:
        text = file_path.read_text(encoding="utf-8")
    except Exception as e:
        print(f"ERROR reading {file_path}: {e}", file=sys.stderr)
        return []

    candidates: list[dict] = []
    lines = text.split("\n")
    sorted_phrases = sorted(link_map.keys(), key=len, reverse=True)

    in_fence = False
    fence_marker = ""
    paragraph_linked: set[str] = set()

    for line_num, line in enumerate(lines, start=1):
        s = line.strip()

        # Track fenced code blocks
        if not in_fence:
            if s.startswith("```") or s.startswith("~~~"):
                in_fence = True
                fence_marker = s[:3]
                continue
        else:
            if s.startswith(fence_marker):
                in_fence = False
            continue

        # Blank line = paragraph boundary
        if s == "":
            paragraph_linked = set()
            continue

        # Skip headings and table separators
        if _is_heading(line) or _is_table_separator(line):
            continue

        for phrase in sorted_phrases:
            if phrase in paragraph_linked:
                continue

            canonical = link_map[phrase]

            # Skip if already linked in this line
            if f"[[{canonical}]]" in line or f"[[{canonical}|" in line:
                continue

            pattern = r"(?<!\w)" + re.escape(phrase) + r"(?!\w)"

            for m in re.finditer(pattern, line):
                pos = m.start()
                if (
                    _in_inline_code(line, pos)
                    or _in_existing_link(line, pos)
                    or _in_url(line, pos)
                ):
                    continue

                paragraph_linked.add(phrase)
                candidates.append({
                    "file": str(file_path.relative_to(vault_root)).replace("\\", "/"),
                    "phrase": m.group(0),
                    "canonical": canonical,
                    "line_num": line_num,
                    "line_text": line.strip()[:120],
                })
                break  # one candidate per phrase per paragraph

    return candidates


# ── Link application ───────────────────────────────────────────────────────────

def apply_links_to_file(
    file_path: Path,
    link_map: dict[str, str],
) -> tuple[int, str | None]:
    """
    Apply links to a file. Returns (links_applied, new_content).
    Returns (0, None) on error.
    """
    try:
        text = file_path.read_text(encoding="utf-8")
    except Exception as e:
        print(f"ERROR reading {file_path}: {e}", file=sys.stderr)
        return 0, None

    lines = text.split("\n")
    new_lines: list[str] = []
    total_applied = 0
    in_fence = False
    fence_marker = ""
    paragraph_linked: set[str] = set()

    sorted_phrases = sorted(link_map.keys(), key=len, reverse=True)

    for line in lines:
        s = line.strip()

        # Track fenced code blocks
        if not in_fence:
            if s.startswith("```") or s.startswith("~~~"):
                in_fence = True
                fence_marker = s[:3]
                new_lines.append(line)
                continue
        else:
            if s.startswith(fence_marker):
                in_fence = False
            new_lines.append(line)
            continue

        if s == "":
            paragraph_linked = set()
            new_lines.append(line)
            continue

        if _is_heading(line) or _is_table_separator(line):
            new_lines.append(line)
            continue

        working = line

        for phrase in sorted_phrases:
            if phrase in paragraph_linked:
                continue

            canonical = link_map[phrase]

            if f"[[{canonical}]]" in working or f"[[{canonical}|" in working:
                continue

            pattern = r"(?<!\w)" + re.escape(phrase) + r"(?!\w)"

            # Capture current state for the replacer closure
            captured_working = working
            captured_canonical = canonical
            captured_phrase = phrase

            def make_replacer(cn: str, ph: str, ctx: str):
                def replacer(m: re.Match) -> str:
                    pos = m.start()
                    if (
                        _in_inline_code(ctx, pos)
                        or _in_existing_link(ctx, pos)
                        or _in_url(ctx, pos)
                    ):
                        return m.group(0)
                    matched = m.group(0)
                    return f"[[{cn}]]" if matched == cn else f"[[{cn}|{matched}]]"
                return replacer

            new_line, count = re.subn(
                pattern,
                make_replacer(captured_canonical, captured_phrase, captured_working),
                working,
                count=1,
            )

            if count > 0:
                working = new_line
                total_applied += count
                paragraph_linked.add(phrase)

        new_lines.append(working)

    return total_applied, "\n".join(new_lines)


# ── Backup ─────────────────────────────────────────────────────────────────────

def create_backup(vault_root: Path, safe_files: list[Path]) -> Path:
    """Copy all safe .md files to a timestamped backup directory."""
    ts = datetime.now().strftime("%Y-%m-%d_%H%M%S")
    backup_dir = BACKUPS_DIR / f"linking_backup_{ts}"
    backup_dir.mkdir(parents=True, exist_ok=True)
    for f in safe_files:
        rel = f.relative_to(vault_root)
        dest = backup_dir / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(f, dest)
    return backup_dir


# ── Report ─────────────────────────────────────────────────────────────────────

def write_report(
    candidates: list[dict],
    files_scanned: int,
    mode: str,
    links_applied: int = 0,
    changed_files: list[tuple[str, int]] | None = None,
) -> None:
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    canonical_counts = Counter(c["canonical"] for c in candidates)
    top_nodes = canonical_counts.most_common(20)

    out: list[str] = [
        "# Link Candidates Report",
        "",
        f"Generated: {now}",
        f"Mode: `{mode}`",
        f"Files scanned: {files_scanned}",
        f"Total candidates found: {len(candidates)}",
        f"Links applied: {links_applied}",
        "",
        "## Top Linked Nodes",
        "",
        "| Node | Candidate Count |",
        "|---|---|",
    ]
    for node, count in top_nodes:
        out.append(f"| [[{node}]] | {count} |")

    out += [
        "",
        "## Candidate Details",
        "",
        "_(First 200 of " + str(len(candidates)) + " candidates)_" if len(candidates) > 200 else "",
        "",
        "| File | Line | Phrase | Canonical | Preview |",
        "|---|---|---|---|---|",
    ]
    for c in candidates[:200]:
        preview = c["line_text"].replace("|", "\\|")
        out.append(
            f"| {c['file']} | {c['line_num']} | {c['phrase']} "
            f"| [[{c['canonical']}]] | {preview} |"
        )

    if changed_files:
        out += [
            "",
            "## Changed Files",
            "",
            "| File | Links Applied |",
            "|---|---|",
        ]
        for fname, cnt in changed_files:
            out.append(f"| {fname} | {cnt} |")

    LINK_CANDIDATES_FILE.write_text("\n".join(out), encoding="utf-8")
    print(f"Report written: {LINK_CANDIDATES_FILE}")


# ── Main ───────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="JARVIS Knowledge Graph Linker")
    parser.add_argument("--dry-run", action="store_true", help="Scan only, no files modified")
    parser.add_argument("--apply", action="store_true", help="Apply links (creates backup first)")
    args = parser.parse_args()

    if not args.dry_run and not args.apply:
        parser.print_help()
        sys.exit(1)

    mode = "DRY-RUN" if args.dry_run else "APPLY"
    print("=== JARVIS Knowledge Graph Linker ===")
    print(f"Vault root: {VAULT_ROOT}")
    print(f"Mode: {mode}")
    print()

    canonical_nodes = parse_canonical_nodes(CANONICAL_NODES_FILE)
    aliases = parse_aliases(ALIASES_FILE)
    link_map = build_link_map(canonical_nodes, aliases)

    print(f"Auto-link canonical nodes: {len(canonical_nodes)}")
    print(f"Aliases loaded:            {len(aliases)}")
    print(f"Total link phrases:        {len(link_map)}")
    print()

    safe_files = get_safe_files(VAULT_ROOT)
    print(f"Safe .md files found: {len(safe_files)}")
    print()

    all_candidates: list[dict] = []
    for f in safe_files:
        candidates = find_candidates_in_file(f, link_map, VAULT_ROOT)
        all_candidates.extend(candidates)

    print(f"Total link candidates: {len(all_candidates)}")
    print()

    top = Counter(c["canonical"] for c in all_candidates).most_common(10)
    if top:
        print("Top candidate nodes:")
        for node, count in top:
            print(f"  [[{node}]]: {count}")
        print()

    if args.dry_run:
        write_report(all_candidates, len(safe_files), "dry-run")
        print()
        print("DRY-RUN COMPLETE. No files were modified.")
        print("Review 10_graph_schema/link_candidates.md before running --apply.")
        return

    # Apply mode — backup first
    print("Creating backup of all safe .md files...")
    backup_dir = create_backup(VAULT_ROOT, safe_files)
    print(f"Backup: {backup_dir}")
    print()

    total_links = 0
    changed_files: list[tuple[str, int]] = []

    for f in safe_files:
        count, new_content = apply_links_to_file(f, link_map)
        if count > 0 and new_content is not None:
            f.write_text(new_content, encoding="utf-8")
            rel = str(f.relative_to(VAULT_ROOT)).replace("\\", "/")
            changed_files.append((rel, count))
            total_links += count

    print(f"Total links applied: {total_links}")
    print(f"Files changed:       {len(changed_files)}")
    for fname, cnt in changed_files:
        print(f"  {fname}: {cnt} links")
    print()

    write_report(
        all_candidates,
        len(safe_files),
        "apply — COMPLETE",
        links_applied=total_links,
        changed_files=changed_files,
    )
    print("APPLY COMPLETE.")


if __name__ == "__main__":
    main()
