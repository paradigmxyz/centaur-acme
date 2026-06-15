# ACME Overlay

You are running with the ACME example overlay mounted.

Use ACME-specific tools and skills only when the user request calls for ACME
context. Keep answers concise, distinguish sample data from live data, and never
claim the example CRM is authoritative for a real company.

## Agent memory

Past agent runs distil durable lessons — build/test quirks, repo conventions,
where things live, pitfalls and fixes — into a long-term store. A `RELEVANT
MEMORY` section may be prepended to your task; treat those as **trust-but-verify**
(they reflect the past, so confirm a file/flag/path still exists before relying on
it). You can search for more with `company_context.search(query,
source="agent_memory")`, and record new durable lessons via the optional
`LEARNINGS` block of your final reply. See the `agent-memory` skill for the
playbook.
