"""Nightly distillation of agent-run learnings into long-term memory.

Every agentic workflow that opts in records a per-run digest (its self-reported
LEARNINGS plus a clipped final report) via `_agent_memory.record_run_digest`. Once
a night this workflow drains those digests, asks one agent turn to distill them
into atomic, deduplicated, scoped memories — reconciling against what already
exists (insert / update / supersede) — writes the canonical `agent_memory` table,
projects each active memory into `company_context_documents` so the built-in
`company_context` tool can search them in-session, and posts a short digest to
Slack so humans get visibility and can correct a bad memory.

All the logic lives in the reusable `_agent_memory` module; this file is the thin
durable wrapper. Gated by `AGENT_MEMORY_ENABLED` (off → no-op). Idempotent and
checkpointed: the single agent turn suspends the run, so on resume the reads
re-run harmlessly, the turn replays from cache, and the one write phase is a
checkpointed step doing ON CONFLICT upserts.

See `.agents/skills/agent-memory/SKILL.md` and the README "Agent memory" section.
"""

import _agent_memory as mem

WORKFLOW_NAME = "nightly_agent_memory"
CRON = "0 9 * * *"  # daily; exact hour is not significant for a digest
SLACK_CHANNEL = "agent-memory-digests"  # change to your channel


async def handler(inp, ctx):
    if not mem.enabled():
        ctx.log("agent_memory_disabled")
        return {"status": "disabled"}

    pool = ctx._pool
    await mem.ensure_schema(pool)

    # 1. Drain pending digests (our own queue; deterministic read, replay-safe).
    pending = await mem.drain_pending(pool, days=mem.lookback_days(), limit=mem.max_runs())
    if not pending:
        return {"status": "noop", "pending": 0}

    run_ids = [r["run_id"] for r in pending]
    # Lowercase repo scopes to match the canonicalized scopes the distiller stores.
    repos_today = sorted({rp.lower() for r in pending for rp in (r["repos"] or [])})
    scopes = ["global"] + [f"repo:{r}" for r in repos_today]
    existing = await mem.existing_for(pool, scopes)

    # 2. One agent turn distills + reconciles → a JSON plan.
    result = await ctx.run_agent("distill", text=mem.distill_prompt(pending, existing))
    text = str((result or {}).get("result_text") or "")
    # An empty reply means the turn didn't really produce anything — bail WITHOUT
    # stamping so the digests stay pending and the next run retries (otherwise an
    # empty reply would read as "no candidates" and drop the runs' learnings).
    if not text.strip():
        ctx.log("agent_memory_turn_empty", pending=len(pending))
        return {"status": "turn_empty", "pending": len(pending)}

    memories = [m for m in (mem.normalize(o) for o in mem.extract_json_array(text)) if m]

    # A real reply with no usable candidates: stamp the drained runs so we don't
    # reprocess them every night.
    if not memories:
        await ctx.step("mark_empty", lambda: mem.mark_extracted(pool, run_ids))
        ctx.log("agent_memory_no_candidates", pending=len(pending))
        return {"status": "no_candidates", "pending": len(pending)}

    # 3. Apply (write phase) — checkpointed so it runs once; idempotent anyway.
    applied = await ctx.step("apply", lambda: mem.apply(pool, memories, run_ids))

    # 4. Human-visible digest + correction loop.
    await ctx.post_to_slack(SLACK_CHANNEL, mem.digest_message(applied, memories))
    ctx.log("agent_memory_done", **{k: applied[k] for k in ("written", "superseded", "runs")})
    return {"status": "ok", **applied}
