# archivero — notes for Claude

- Inventory and cleanup tool for old backups. Start from `docs/ARCHITECTURE.md`; decisions are in `docs/adr/`,
  OS layout rules in `docs/OS_LAYOUTS.md`, plans in `docs/ROADMAP.md`. Open only what the task needs.
- Conversation with the owner (Isaac) is in Spanish; code, docs, commits and PRs are in English.
- Never delete, move or modify source files (ADR 0001): cleanup is proposals only, `apply` only copies.
- memento depends on the SQLite schema (`files`, `members`, `media`): any change there bumps `SCHEMA_VERSION`.
- Run `python -m pytest -q` before committing; `scripts/demo_tree.py` builds a test tree.
- A new or changed decision gets a new ADR (copy `docs/adr/0000-template.md`, next number, add it to the index);
  never rewrite an accepted one — supersede it.
- One PR per milestone; README and docs updated in the same PR.
- Commits as `Isaac Agudo <isaacagudo@users.noreply.github.com>`.
