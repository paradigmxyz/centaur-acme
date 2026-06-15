"""Reusable long-term agent-memory library for a Centaur overlay.

Centaur runs agentic work in throwaway sandboxes, so anything an agent learns in
a run — repo conventions, build/test quirks, where things live, pitfalls and how
they were resolved — is normally lost when the sandbox is torn down. This module
gives those learnings a durable home across runs, in three stages:

    capture  →  nightly distill  →  recall
    (an agent emits an          (one agent turn        (future runs get the
     optional LEARNINGS          deduplicates the        relevant lessons in
     block; the workflow         day's learnings into    their prompt, and can
     records a digest)           atomic memories)        search them on demand)

This file is the reusable core. It is `_`-prefixed so Centaur's workflow loader
skips it during discovery (it has no ``WORKFLOW_NAME``), while sibling workflow
files import it — the loader puts each `WORKFLOW_DIRS` entry on ``sys.path`` (see
`extend/overlay.md`), so `import _agent_memory` resolves within this overlay. It
is deliberately a top-level module (not a ``workflows`` package) so it never
shadows the base platform's ``workflows`` package.

Storage is self-ensuring: ``ensure_schema()`` runs ``CREATE TABLE IF NOT EXISTS``
so the overlay works on any stock Centaur without depending on a migration being
applied. Recall projects active memories into ``company_context_documents`` so
the built-in ``company_context`` tool can search them in-session; that projection
and search are optional enrichment — the prompt-pack recall works on its own.

Everything here is best-effort: if the feature flag is off or the database is
briefly unavailable, capture/recall degrade to no-ops and never disturb a run.

Configuration (environment, all optional):
    AGENT_MEMORY_ENABLED                 master switch (default off)
    AGENT_MEMORY_LOOKBACK_DAYS           nightly drain window (default 7)
    AGENT_MEMORY_MAX_RUNS                digests distilled per night (default 40)
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import re
from typing import Any

ENABLED_FLAG = "AGENT_MEMORY_ENABLED"
SOURCE = "agent_memory"  # company_context_documents.source for projected memories
KINDS = ("convention", "pitfall", "fix_pattern", "env_quirk", "test_infra", "arch_note")

# A LEARNINGS line: `<scope> | <kind> | <one-line lesson>`.
LEARNING_RE = re.compile(
    r"(?im)^\s*[-*]\s*(global|repo:[\w./-]+|area:[\w./-]+)\s*\|\s*([\w-]+)\s*\|\s*(.+?)\s*$"
)


# ── Config + small utilities ─────────────────────────────────────────────────


def enabled() -> bool:
    return (os.getenv(ENABLED_FLAG, "") or "").strip().lower() in ("1", "true", "yes", "on")


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, "").strip() or default)
    except ValueError:
        return default


def lookback_days() -> int:
    return _env_int("AGENT_MEMORY_LOOKBACK_DAYS", 7)


def max_runs() -> int:
    return _env_int("AGENT_MEMORY_MAX_RUNS", 40)


def _clip(text: str, n: int) -> str:
    text = (text or "").strip()
    return text if len(text) <= n else text[: n - 1].rstrip() + "…"


def _clean_str_list(values, *, limit: int | None = None) -> list[str]:
    out = [s for s in (str(v).strip() for v in (values or [])) if s]
    return out[:limit] if limit else out


def memory_id(scope: str, title: str) -> str:
    """Deterministic id so re-running apply upserts the same row instead of duplicating."""
    digest = hashlib.sha1(f"{scope.strip().lower()}|{title.strip().lower()}".encode()).hexdigest()
    return "mem_" + digest[:24]


def extract_json_array(text: str) -> list[dict[str, Any]]:
    """Tolerant parse of a JSON array of objects from an agent reply (fenced or bare)."""
    if not text:
        return []
    candidates: list[str] = []
    fence = re.search(r"```(?:json)?\s*(\[.*?\])\s*```", text, re.DOTALL)
    if fence:
        candidates.append(fence.group(1))
    start, end = text.find("["), text.rfind("]")
    if start != -1 and end > start:
        candidates.append(text[start : end + 1])
    for raw in candidates:
        try:
            data = json.loads(raw)
        except (ValueError, TypeError):
            continue
        if isinstance(data, list):
            return [d for d in data if isinstance(d, dict)]
    return []


# ── Storage (self-ensuring schema) ───────────────────────────────────────────

_SCHEMA_DDL = """
CREATE TABLE IF NOT EXISTS agent_memory (
    memory_id        TEXT PRIMARY KEY,
    scope            TEXT NOT NULL,
    kind             TEXT NOT NULL,
    title            TEXT NOT NULL,
    body             TEXT NOT NULL,
    tags             TEXT[]  NOT NULL DEFAULT '{}',
    repos            TEXT[]  NOT NULL DEFAULT '{}',
    confidence       REAL    NOT NULL DEFAULT 0.5,
    status           TEXT    NOT NULL DEFAULT 'active',
    superseded_by    TEXT    REFERENCES agent_memory(memory_id),
    source_run_ids   TEXT[]  NOT NULL DEFAULT '{}',
    source_issue     TEXT    NOT NULL DEFAULT '',
    source_prs       TEXT[]  NOT NULL DEFAULT '{}',
    times_surfaced   INT     NOT NULL DEFAULT 0,
    last_surfaced_at TIMESTAMPTZ,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CHECK (scope <> ''),
    CHECK (kind <> ''),
    CHECK (title <> '')
);
CREATE INDEX IF NOT EXISTS idx_agent_memory_lookup ON agent_memory (status, scope);
CREATE INDEX IF NOT EXISTS idx_agent_memory_tags   ON agent_memory USING GIN (tags);
CREATE INDEX IF NOT EXISTS idx_agent_memory_repos  ON agent_memory USING GIN (repos);

CREATE TABLE IF NOT EXISTS agent_run_digest (
    run_id          TEXT PRIMARY KEY,
    workflow_name   TEXT NOT NULL DEFAULT '',
    identifier      TEXT NOT NULL DEFAULT '',
    repos           TEXT[] NOT NULL DEFAULT '{}',
    context_pct     REAL,
    learnings_raw   JSONB NOT NULL DEFAULT '[]'::jsonb,
    digest_md       TEXT NOT NULL DEFAULT '',
    extracted_at    TIMESTAMPTZ,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_agent_run_digest_pending
    ON agent_run_digest (created_at) WHERE extracted_at IS NULL;
"""


async def ensure_schema(pool) -> bool:
    """Create the two memory tables if absent. Idempotent (IF NOT EXISTS) and
    best-effort — a failure returns False rather than raising, so a caller's run
    is never disturbed by transient DB issues.

    `agent_run_digest.identifier`/`context_pct` and `agent_memory.source_issue`/
    `source_prs` are optional capture fields: the distiller never requires them,
    so overlays whose agents don't track an issue id or context % can ignore them.
    """
    try:
        await pool.execute(_SCHEMA_DDL)
        return True
    except Exception:  # noqa: BLE001 — schema setup is best-effort
        return False


# ── Capture (called by your agentic workflows) ───────────────────────────────


def parse_learnings(text: str) -> list[dict[str, str]]:
    """An agent's optional LEARNINGS block → atomic {scope, kind, body} records.

    Each line is `<scope> | <kind> | <one-line lesson>`. Returns [] when none are
    present — the block is optional, a malformed line is skipped, an echoed
    template line is dropped, results are deduped within one report and capped so
    a runaway reply can't bloat the digest.
    """
    out: list[dict[str, str]] = []
    seen: set[tuple[str, str, str]] = set()
    for m in LEARNING_RE.finditer(text or ""):
        scope, kind, body = m.group(1).strip(), m.group(2).strip(), m.group(3).strip()
        low = body.lower()
        if low.startswith("<one-line") or "worth remembering" in low or not body:
            continue
        key = (scope, kind, body)
        if key in seen:
            continue
        seen.add(key)
        out.append({"scope": scope, "kind": kind, "body": body})
    return out[:8]


async def record_run_digest(
    pool, *, run_id: str, workflow_name: str = "", identifier: str = "",
    repos: list[str] | None = None, report_text: str = "",
    learnings: list[dict[str, str]] | None = None, context_pct: float | None = None,
) -> dict[str, Any]:
    """Capture one agent turn's self-reported learnings for the nightly distiller.

    Idempotent per run (ON CONFLICT); learnings accumulate across a run's turns.
    Never raises — a DB error is swallowed so the calling run is never disturbed.
    """
    if not enabled() or not run_id:
        return {"ok": False, "skipped": True}
    try:
        await pool.execute(
            """
            INSERT INTO agent_run_digest
                (run_id, workflow_name, identifier, repos, context_pct,
                 learnings_raw, digest_md)
            VALUES ($1, $2, $3, $4::text[], $5, $6::jsonb, $7)
            ON CONFLICT (run_id) DO UPDATE SET
                learnings_raw = agent_run_digest.learnings_raw || EXCLUDED.learnings_raw,
                digest_md     = EXCLUDED.digest_md,
                context_pct   = COALESCE(EXCLUDED.context_pct, agent_run_digest.context_pct),
                identifier    = EXCLUDED.identifier,
                repos         = EXCLUDED.repos,
                updated_at    = NOW()
            """,
            run_id, workflow_name, identifier, _clean_str_list(repos),
            context_pct, json.dumps(learnings or []), _clip(report_text or "", 6000),
        )
    except Exception as exc:  # noqa: BLE001 — capture is best-effort, never fatal
        return {"ok": False, "error": str(exc)}
    return {"ok": True, "learnings": len(learnings or [])}


# ── Recall (called by your agentic workflows) ────────────────────────────────


async def memory_pack(pool, scopes: list[str], *, budget: int = 1800, limit: int = 14) -> str:
    """A RELEVANT MEMORY prompt section for the given scopes, or '' if none.

    Best-effort: any failure (flag off, missing table, DB hiccup) yields '' so the
    agent prompt is unaffected. Surfaced rows get a best-effort fairness bump
    (times_surfaced / last_surfaced_at) so equal-confidence memories rotate
    instead of the same top-N showing every run; a failed bump never affects the
    prompt. `scopes` is e.g. ["global", "repo:owner/name", "area:frontend"].
    """
    if not enabled() or not scopes:
        return ""
    try:
        rows = await pool.fetch(
            """
            SELECT memory_id, scope, kind, title, body
            FROM agent_memory
            WHERE status = 'active' AND scope = ANY($1::text[])
            ORDER BY confidence DESC, last_surfaced_at ASC NULLS FIRST, updated_at DESC
            LIMIT $2
            """,
            list(scopes), int(limit),
        )
    except Exception:  # noqa: BLE001 — memory recall is decoration, never fatal
        return ""
    if not rows:
        return ""
    header = (
        "RELEVANT MEMORY (durable lessons from past agent runs — trust but "
        "verify; they may be stale, so confirm a file/flag/path still exists "
        "before relying on it)\n"
    )
    lines: list[str] = []
    surfaced: list[str] = []
    used = len(header)
    for r in rows:
        scope = (r["scope"] or "").replace("repo:", "")
        body = " ".join((r["body"] or "").split())
        if len(body) > 200:
            body = body[:199].rstrip() + "…"
        line = f"- [{scope} · {r['kind']}] {r['title']} — {body}\n"
        if used + len(line) > budget:
            break
        lines.append(line)
        surfaced.append(r["memory_id"])
        used += len(line)
    if not lines:
        return ""
    try:  # fairness bump — best-effort, never fatal
        await pool.execute(
            "UPDATE agent_memory SET times_surfaced = times_surfaced + 1, "
            "last_surfaced_at = NOW() WHERE memory_id = ANY($1::text[])",
            surfaced,
        )
    except Exception:  # noqa: BLE001
        pass
    return header + "".join(lines) + "\n"


# ── Nightly distillation (used by the nightly_agent_memory workflow) ─────────


async def drain_pending(pool, *, days: int, limit: int) -> list[Any]:
    """The un-extracted run digests to distill this run, newest-eligible first by
    age, joined to workflow_runs for each run's terminal outcome (best-effort)."""
    return await pool.fetch(
        """
        SELECT d.run_id, d.workflow_name, d.identifier, d.repos, d.digest_md,
               d.learnings_raw, d.context_pct,
               COALESCE(r.output_json->>'status', '') AS outcome
        FROM agent_run_digest d
        LEFT JOIN workflow_runs r ON r.run_id = d.run_id
        WHERE d.extracted_at IS NULL
          AND d.created_at > NOW() - make_interval(days => $1::int)
        ORDER BY d.created_at
        LIMIT $2
        """,
        int(days), int(limit),
    )


async def existing_for(pool, scopes: list[str], *, limit: int = 200) -> list[Any]:
    """Active memories in the candidate scopes, for the distiller to reconcile against."""
    if not scopes:
        return []
    return await pool.fetch(
        """
        SELECT memory_id, scope, kind, title, body, tags, confidence
        FROM agent_memory
        WHERE status = 'active' AND scope = ANY($1::text[])
        ORDER BY confidence DESC, updated_at DESC
        LIMIT $2
        """,
        list(scopes), int(limit),
    )


def _runs_blob(rows: list[Any]) -> str:
    """Render pending run digests as compact, bounded prompt material."""
    out: list[str] = []
    for r in rows:
        learnings = r["learnings_raw"]
        if isinstance(learnings, str):
            try:
                learnings = json.loads(learnings)
            except (ValueError, TypeError):
                learnings = []
        repos = ", ".join(r["repos"] or []) or "(unknown)"
        out.append(f"### Run {r['identifier'] or r['run_id']} — {r['workflow_name']} — repos: {repos}")
        if r["outcome"]:
            out.append(f"outcome: {r['outcome']}")
        # learnings_raw concatenates each turn's block, so a multi-turn run that
        # repeats the same LEARNINGS accumulates duplicates — dedup before they
        # bias the distiller prompt.
        seen: set[tuple] = set()
        deduped = []
        for rec in learnings if isinstance(learnings, list) else []:
            if not isinstance(rec, dict):
                continue
            key = (rec.get("scope"), rec.get("kind"), rec.get("body"))
            if key not in seen:
                seen.add(key)
                deduped.append(rec)
        if deduped:
            out.append("self-reported LEARNINGS:")
            for rec in deduped:
                out.append(
                    f"- {rec.get('scope', '?')} | {rec.get('kind', '?')} | {rec.get('body', '')}"
                )
        if r["digest_md"]:
            out.append("final report (clipped):")
            out.append(_clip(r["digest_md"], 1500))
        out.append("")
    return "\n".join(out)


def _existing_blob(rows: list[Any]) -> str:
    """Render existing memories so the turn can reconcile (update / supersede)."""
    if not rows:
        return "(none yet for these repos)"
    out: list[str] = []
    for r in rows:
        tags = ", ".join(r["tags"] or [])
        out.append(
            f"- id={r['memory_id']} | {r['scope']} | {r['kind']} | conf={r['confidence']:.2f}"
            f" | tags=[{tags}]\n  {r['title']} — {_clip(r['body'], 240)}"
        )
    return "\n".join(out)


_PROMPT = """You are the memory curator for this deployment's autonomous agents. Those
agents run in throwaway sandboxes; your job is to turn what they learned recently
into a small set of durable, reusable memories that future runs on the same repos
will benefit from.

Below are (A) recent agent runs with their self-reported learnings and clipped
final reports, and (B) the memories that ALREADY exist for these repos.

Produce a JSON array of memory objects to write. Rules:
- ATOMIC: one concrete, reusable fact per memory (a build/test quirk, a repo
  convention, where something lives, a pitfall + how to avoid it). NOT a summary
  of one task, NOT anything specific to a single ticket, NOT transient status. If
  a "learning" is just a restated task, drop it.
- SCOPE each memory: "global" (true across all repos), "repo:owner/name", or
  "area:short-tag".
- KIND is one of: convention | pitfall | fix_pattern | env_quirk | test_infra | arch_note.
- RECONCILE against existing memories: if a new fact duplicates an existing one,
  reference its id in "memory_id" to UPDATE it (raise confidence, sharpen the
  wording) rather than adding a duplicate. If a new fact CONTRADICTS or replaces
  an existing one, set "supersedes" to the list of old ids it replaces.
- Prefer FEW high-signal memories over many. It is fine to return an empty array.
- "body" may include short **Why:** and **How to apply:** lines when useful.
- CONFIDENCE 0.0-1.0: how sure/reusable this is.
- Never include secrets, credentials, or content from confidential channels.

Output ONLY the JSON array (no prose, no code fence needed). Each object:
{"memory_id": "<existing id to update, or omit for new>",
 "scope": "...", "kind": "...", "title": "<=80 chars",
 "body": "the durable fact", "tags": ["..."], "confidence": 0.7,
 "supersedes": ["<old id>", ...]}
"""


def distill_prompt(pending: list[Any], existing: list[Any]) -> str:
    """Render the distiller prompt by single-pass concatenation: the data sections
    are appended, never substituted into a template, so run/memory text may contain
    ANY characters (braces, or a literal '{runs}') without corrupting the prompt.
    _PROMPT keeps its literal JSON example, so it is never run through str.format."""
    return (
        f"{_PROMPT}\n"
        f"=== (A) RECENT RUNS ===\n{_runs_blob(pending)}\n\n"
        f"=== (B) EXISTING MEMORIES FOR THESE REPOS ===\n{_existing_blob(existing)}\n"
    )


def normalize(obj: dict[str, Any]) -> dict[str, Any] | None:
    """Validate/clean one model-proposed memory; return None to drop it."""
    scope = str(obj.get("scope") or "").strip()
    # repo names are case-insensitive; canonicalize so the case-sensitive
    # `scope = ANY(...)` recall query (fed lowercased repo scopes) always matches.
    if scope.lower().startswith("repo:"):
        scope = "repo:" + scope[5:].lower()
    kind = str(obj.get("kind") or "").strip()
    title = _clip(str(obj.get("title") or ""), 200)
    body = str(obj.get("body") or "").strip()
    if not scope or not title or not body or kind not in KINDS:
        return None
    try:
        conf = float(obj.get("confidence", 0.6))
    except (ValueError, TypeError):
        conf = 0.6
    conf = max(0.0, min(1.0, conf))
    tags = _clean_str_list(obj.get("tags"), limit=8)
    supersedes = _clean_str_list(obj.get("supersedes"))
    existing_id = str(obj.get("memory_id") or "").strip()
    mid = existing_id if existing_id.startswith("mem_") else memory_id(scope, title)
    repos = [scope.split(":", 1)[1]] if scope.startswith("repo:") else []
    return {
        "memory_id": mid, "scope": scope, "kind": kind, "title": title,
        "body": body, "tags": tags, "confidence": conf, "repos": repos,
        "supersedes": [s for s in supersedes if s != mid],
    }


def _proj_metadata(mem: dict[str, Any]) -> str:
    return json.dumps({
        "scope": mem["scope"], "kind": mem["kind"], "repos": mem["repos"],
        "tags": mem["tags"], "confidence": mem["confidence"],
    })


def _affected(status: str) -> int:
    # asyncpg returns a command tag like "UPDATE 3"; parse the trailing count.
    try:
        return int(str(status).rsplit(" ", 1)[-1])
    except (ValueError, IndexError):
        return 0


async def mark_extracted(pool, run_ids: list[str]) -> int:
    """Stamp drained digests done. Idempotent (the WHERE skips already-stamped)."""
    if not run_ids:
        return 0
    status = await pool.execute(
        "UPDATE agent_run_digest SET extracted_at = NOW() "
        "WHERE run_id = ANY($1::text[]) AND extracted_at IS NULL",
        list(run_ids),
    )
    return _affected(status)


async def apply(pool, memories: list[dict[str, Any]], run_ids: list[str]) -> dict[str, Any]:
    """Upsert memories, supersede replaced ones, project active rows into
    company_context_documents (so the company_context tool can search them), and
    stamp the drained digests. Idempotent: deterministic ids + ON CONFLICT, so a
    replay re-applies to the same end state."""
    written = superseded = projected = 0
    retired: set[str] = set()  # old memory_ids whose search projection to drop
    # Ids written active this batch must never be superseded/unprojected: the
    # model controls both memory_id and supersedes, so it could re-emit an id it
    # also supersedes — that would leave a 'superseded' row with a live search
    # projection (an "undead" memory). Exclude them from supersession.
    active_ids = {mem["memory_id"] for mem in memories}
    now = dt.datetime.now(dt.timezone.utc)
    for mem in memories:
        await pool.execute(
            """
            INSERT INTO agent_memory
                (memory_id, scope, kind, title, body, tags, repos, confidence,
                 status, source_run_ids, updated_at)
            VALUES ($1,$2,$3,$4,$5,$6::text[],$7::text[],$8,'active',$9::text[],NOW())
            ON CONFLICT (memory_id) DO UPDATE SET
                kind = EXCLUDED.kind,
                title = EXCLUDED.title,
                body = EXCLUDED.body,
                tags = EXCLUDED.tags,
                repos = EXCLUDED.repos,
                confidence = GREATEST(agent_memory.confidence, EXCLUDED.confidence),
                status = 'active',
                superseded_by = NULL,
                source_run_ids = ARRAY(
                    SELECT DISTINCT unnest(agent_memory.source_run_ids || EXCLUDED.source_run_ids)
                ),
                updated_at = NOW()
            """,
            mem["memory_id"], mem["scope"], mem["kind"], mem["title"], mem["body"],
            mem["tags"], mem["repos"], mem["confidence"], list(run_ids),
        )
        written += 1

        sup = [s for s in mem["supersedes"] if s not in active_ids]
        if sup:
            status = await pool.execute(
                """
                UPDATE agent_memory
                SET status = 'superseded', superseded_by = $1, updated_at = NOW()
                WHERE memory_id = ANY($2::text[])
                """,
                mem["memory_id"], sup,
            )
            superseded += _affected(status)
            retired.update(sup)  # projections dropped in one pass below

        # Project the active memory so the in-sandbox company_context tool finds it.
        body = f"{mem['body']}\n\nscope: {mem['scope']} · tags: {', '.join(mem['tags'])}"
        content_hash = hashlib.sha1(f"{mem['title']}|{body}|active".encode()).hexdigest()
        await pool.execute(
            """
            INSERT INTO company_context_documents
                (document_id, source, source_type, source_document_id, source_chunk_id,
                 title, body, url, author_id, author_name, access_scope,
                 occurred_at, source_updated_at, content_hash, metadata)
            VALUES ($1,$2,'agent_memory',$3,'',$4,$5,'','','agent-memory','company',
                    $6,$6,$7,$8::jsonb)
            ON CONFLICT (document_id) DO UPDATE SET
                title = EXCLUDED.title,
                body = EXCLUDED.body,
                content_hash = EXCLUDED.content_hash,
                metadata = EXCLUDED.metadata,
                source_updated_at = EXCLUDED.source_updated_at,
                updated_at = NOW()
            """,
            f"{SOURCE}:{mem['memory_id']}", SOURCE, mem["memory_id"],
            mem["title"], body, now, content_hash, _proj_metadata(mem),
        )
        projected += 1

    # Drop the search projections of everything retired this run, in one pass
    # (active ids were already excluded from `retired` above).
    deleted = 0
    if retired:
        status = await pool.execute(
            "DELETE FROM company_context_documents WHERE document_id = ANY($1::text[])",
            [f"{SOURCE}:{old}" for old in retired],
        )
        deleted = _affected(status)

    extracted = await mark_extracted(pool, run_ids)
    return {"written": written, "superseded": superseded, "projected": projected,
            "deleted": deleted, "runs": extracted}


def digest_message(applied: dict[str, Any], memories: list[dict[str, Any]]) -> str:
    """A short Slack summary of a distillation run."""
    head = (
        f"🧠 *Agent memory* — distilled {applied['runs']} run(s): "
        f"{applied['written']} written, {applied['superseded']} superseded."
    )
    lines = [head]
    for mem in memories[:12]:
        scope = mem["scope"].replace("repo:", "")
        lines.append(f"• [{scope} · {mem['kind']}] {_clip(mem['title'], 110)}")
    if len(memories) > 12:
        lines.append(f"…and {len(memories) - 12} more.")
    lines.append('_Searchable in-session via `company_context.search(query, source="agent_memory")`._')
    return "\n".join(lines)
