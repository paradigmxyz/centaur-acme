"""Example agentic workflow wired into long-term agent memory.

`centaur-acme` ships no real autonomous coding/review agent, so this is a minimal,
sample-data example showing the THREE integration points a real agentic workflow
adds to participate in the memory system (see `_agent_memory` + the nightly
`nightly_agent_memory` distiller):

  1. RECALL  — prepend a `memory_pack` of relevant past lessons to the prompt.
  2. PROMPT  — ask the agent to end with an optional `LEARNINGS` block.
  3. CAPTURE — parse that block and record a run digest for the nightly distiller.

Replace the sample task below with your own agent work (a coding task, a review,
a triage). The memory wiring stays the same. Gated by `AGENT_MEMORY_ENABLED`; with
it off, recall yields an empty pack and capture is a no-op, so the workflow still
runs its task normally.
"""

import _agent_memory as mem

WORKFLOW_NAME = "example_memory_agent"
CRON = "0 13 * * 1-5"  # illustrative; replace with your own trigger
REPO = "acme/example-app"  # the sample "repo" this example pretends to work in

# The capture contract: agents end their reply with this optional block. Keep it
# in sync with `_agent_memory.parse_learnings` (one line per durable lesson).
LEARNINGS_INSTRUCTIONS = (
    "When you finish, if you discovered anything DURABLE and reusable for a future "
    "run on this codebase (a build/test quirk, a convention, where something lives, "
    "a pitfall + how to avoid it), end your reply with a LEARNINGS block — up to 5 "
    "lines, omit it entirely if nothing is durable — formatted EXACTLY as:\n"
    "LEARNINGS\n"
    "- <scope: global | repo:owner/name | area:short-tag> | <kind: convention|"
    "pitfall|fix_pattern|env_quirk|test_infra|arch_note> | <one-line lesson>"
)

TASK = (
    "This is the ACME example overlay (sample data). Propose one small, concrete "
    "improvement to a hypothetical ACME sample service and explain why. Be explicit "
    "that this is an illustrative example, not real work."
)


async def handler(inp, ctx):
    pool = ctx._pool
    await mem.ensure_schema(pool)

    # 1. RECALL: pull durable lessons for this repo (+ global) into the prompt.
    pack = await mem.memory_pack(pool, ["global", f"repo:{REPO}"])

    # 2. PROMPT: relevant memory, the task, and the LEARNINGS capture contract.
    prompt = f"{pack}{TASK}\n\n{LEARNINGS_INSTRUCTIONS}"
    result = await ctx.run_agent("example", text=prompt)
    report = str((result or {}).get("result_text") or "")

    # 3. CAPTURE: record this run's self-reported learnings for the nightly job.
    await ctx.step(
        "record",
        lambda: mem.record_run_digest(
            pool, run_id=ctx.run_id, workflow_name=WORKFLOW_NAME,
            identifier=REPO, repos=[REPO], report_text=report,
            learnings=mem.parse_learnings(report),
        ),
    )
    return {"status": "ok", "learnings": len(mem.parse_learnings(report))}
