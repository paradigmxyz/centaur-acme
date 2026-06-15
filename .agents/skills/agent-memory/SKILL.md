---
name: agent-memory
description: Use when you are an autonomous agent working in a repo and want durable lessons from PAST agent runs — build/test quirks, conventions, where things live, pitfalls and how they were resolved. Recall them by searching company_context with source="agent_memory" before you start and whenever you hit a build/test/convention snag, and trust-but-verify what you find.
---

# Agent Memory

Past agent runs distil what they learned into a long-term memory store (the
nightly `nightly_agent_memory` workflow). Those lessons are exactly the things a
fresh sandbox forgets: how a repo builds and tests, naming and layout
conventions, recurring gotchas and their fixes. Consult them so you don't
re-discover — and re-pay for — what a previous run already figured out.

## When to use

- **At the start of a task**, once you know which repo(s) you'll touch: pull the
  memories for that repo plus the global ones.
- **When you hit friction** — an odd build/test command, an unclear convention,
  "where does X live", a confusing error — check whether a past run hit it too.

A `RELEVANT MEMORY` section may already be prepended to your task by the
workflow; this skill is how you find *more* than the few that were injected.

## Tool

Memories are stored alongside company knowledge and reached with the built-in
`company_context` tool, filtered to the agent-memory source:

- `company_context.search(query, source="agent_memory", limit=10)` → ranked
  memories. Each result has `title`, `body`, and `metadata` (scope, kind, repos,
  tags, confidence). Use concrete nouns / identifiers — the index is BM25.
- `company_context.read_document(document_id)` → the full memory body when the
  preview is thin.

(This needs the `company_context` tool enabled in the deployment. If it isn't,
the prompt-pack `RELEVANT MEMORY` section still works on its own.)

## How to use what you find

1. **Search** with 1–2 focused queries scoped to your repo/topic, e.g.
   `search("vitest config timeout", source="agent_memory")`.
2. **Trust but verify.** A memory reflects a *past* run and may be stale —
   confirm the file/flag/script/path it names still exists before relying on it.
   A wrong memory is worth more corrected than followed blindly.
3. **Apply it** to save a step (use the known-good command, follow the noted
   convention, avoid the known pitfall), but let the current code win on conflict.

## Contributing back

You don't write to this store directly. Instead, end your final reply with the
optional **LEARNINGS** block (one line per durable lesson:
`<scope> | <kind> | <one-line lesson>`). The nightly job dedups and merges those
into memory for the next run. Keep them atomic and reusable — a build/test quirk,
a convention, a pitfall + fix — never ticket-specific status. Never record
secrets or anything from confidential channels.
