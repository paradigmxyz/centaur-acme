"""Unit tests for the reusable agent-memory module (`workflows/_agent_memory.py`).

Pure-helper / fake-pool tests only — no network, no real DB. Coroutines are
driven with asyncio.run (the template ships no async pytest plugin). The module
is loaded the way Centaur's workflow loader exposes it at runtime: the overlay's
`workflows/` dir is on sys.path, so it imports as the top-level `_agent_memory`.
"""

import asyncio
import importlib
import sys
from pathlib import Path

import pytest

WORKFLOWS_DIR = Path(__file__).resolve().parent.parent / "workflows"
if str(WORKFLOWS_DIR) not in sys.path:
    sys.path.insert(0, str(WORKFLOWS_DIR))

mem = importlib.import_module("_agent_memory")


class FakePool:
    """Minimal asyncpg-pool stand-in: canned fetch rows, recorded executes."""

    def __init__(self, rows=None, raise_on=None):
        self._rows = rows if rows is not None else []
        self._raise = raise_on
        self.executes: list[tuple] = []

    async def fetch(self, query, *args):
        if self._raise == "fetch":
            raise RuntimeError("boom")
        return self._rows

    async def execute(self, query, *args):
        if self._raise == "execute":
            raise RuntimeError("boom")
        self.executes.append((query, args))
        return "INSERT 0 1"


# ── enabled ──────────────────────────────────────────────────────────────────


class TestEnabled:
    @pytest.mark.parametrize("val", ["1", "true", "TRUE", "yes", "on"])
    def test_enabled(self, val, monkeypatch):
        monkeypatch.setenv("AGENT_MEMORY_ENABLED", val)
        assert mem.enabled() is True

    @pytest.mark.parametrize("val", ["", "0", "false", "off", "no"])
    def test_disabled(self, val, monkeypatch):
        monkeypatch.setenv("AGENT_MEMORY_ENABLED", val)
        assert mem.enabled() is False

    def test_unset_is_disabled(self, monkeypatch):
        monkeypatch.delenv("AGENT_MEMORY_ENABLED", raising=False)
        assert mem.enabled() is False


# ── parse_learnings ──────────────────────────────────────────────────────────


class TestParseLearnings:
    def test_parses_wellformed_lines(self):
        text = (
            "blah\n"
            "- repo:acme/example-app | env_quirk | pnpm i needs --frozen-lockfile\n"
            "- global | convention | commit messages use conventional-commits\n"
        )
        out = mem.parse_learnings(text)
        assert out == [
            {"scope": "repo:acme/example-app", "kind": "env_quirk",
             "body": "pnpm i needs --frozen-lockfile"},
            {"scope": "global", "kind": "convention",
             "body": "commit messages use conventional-commits"},
        ]

    def test_skips_template_example_line(self):
        text = ("- <scope: global | repo:owner/name | area:x> | <kind: convention> | "
                "<one-line lesson>\n")
        assert mem.parse_learnings(text) == []

    def test_dedupes_within_report(self):
        text = "- global | pitfall | do not push to main\n" * 2
        assert len(mem.parse_learnings(text)) == 1

    @pytest.mark.parametrize("bad", ["", None, "prose only\nCONTEXT_USED: 10%"])
    def test_no_learnings(self, bad):
        assert mem.parse_learnings(bad) == []

    def test_caps_runaway(self):
        lines = "\n".join(f"- global | pitfall | lesson {i}" for i in range(20))
        assert len(mem.parse_learnings(lines)) == 8


# ── memory_pack ──────────────────────────────────────────────────────────────


class TestMemoryPack:
    def _rows(self):
        return [
            {"memory_id": "mem_1", "scope": "repo:o/n", "kind": "env_quirk",
             "title": "Use pnpm", "body": "pnpm install with --frozen-lockfile"},
            {"memory_id": "mem_2", "scope": "global", "kind": "convention",
             "title": "Conv commits", "body": "use conventional commit messages"},
        ]

    def test_disabled_returns_empty(self, monkeypatch):
        monkeypatch.setenv("AGENT_MEMORY_ENABLED", "0")
        assert asyncio.run(mem.memory_pack(FakePool(self._rows()), ["global"])) == ""

    def test_empty_scopes(self, monkeypatch):
        monkeypatch.setenv("AGENT_MEMORY_ENABLED", "1")
        assert asyncio.run(mem.memory_pack(FakePool(self._rows()), [])) == ""

    def test_no_rows(self, monkeypatch):
        monkeypatch.setenv("AGENT_MEMORY_ENABLED", "1")
        assert asyncio.run(mem.memory_pack(FakePool([]), ["global"])) == ""

    def test_renders_and_bumps(self, monkeypatch):
        monkeypatch.setenv("AGENT_MEMORY_ENABLED", "1")
        pool = FakePool(self._rows())
        pack = asyncio.run(mem.memory_pack(pool, ["global", "repo:o/n"]))
        assert pack.startswith("RELEVANT MEMORY")
        assert "[o/n · env_quirk] Use pnpm" in pack
        assert "[global · convention] Conv commits" in pack
        assert pack.endswith("\n\n")
        bumps = [c for c in pool.executes if "times_surfaced" in c[0]]
        assert len(bumps) == 1 and bumps[0][1][0] == ["mem_1", "mem_2"]

    def test_fetch_error_swallowed(self, monkeypatch):
        monkeypatch.setenv("AGENT_MEMORY_ENABLED", "1")
        assert asyncio.run(mem.memory_pack(FakePool(raise_on="fetch"), ["global"])) == ""

    def test_bump_error_swallowed(self, monkeypatch):
        monkeypatch.setenv("AGENT_MEMORY_ENABLED", "1")
        pack = asyncio.run(mem.memory_pack(FakePool(self._rows(), raise_on="execute"), ["global"]))
        assert pack.startswith("RELEVANT MEMORY")

    def test_budget_truncates(self, monkeypatch):
        monkeypatch.setenv("AGENT_MEMORY_ENABLED", "1")
        rows = [{"memory_id": f"mem_{i}", "scope": "global", "kind": "convention",
                 "title": f"T{i}", "body": "x" * 100} for i in range(20)]
        pack = asyncio.run(mem.memory_pack(FakePool(rows), ["global"], budget=400))
        assert len(pack) <= 420 and pack.count("\n- ") <= 5


# ── record_run_digest ────────────────────────────────────────────────────────


class TestRecordRunDigest:
    def _call(self, pool):
        return asyncio.run(mem.record_run_digest(
            pool, run_id="run-1", workflow_name="wf", identifier="X",
            repos=["o/n"], report_text="report",
            learnings=[{"scope": "global", "kind": "pitfall", "body": "x"}],
        ))

    def test_disabled_skips(self, monkeypatch):
        monkeypatch.setenv("AGENT_MEMORY_ENABLED", "0")
        pool = FakePool()
        assert self._call(pool)["skipped"] is True and pool.executes == []

    def test_writes_once(self, monkeypatch):
        monkeypatch.setenv("AGENT_MEMORY_ENABLED", "1")
        pool = FakePool()
        assert self._call(pool) == {"ok": True, "learnings": 1}
        assert len(pool.executes) == 1 and "agent_run_digest" in pool.executes[0][0]

    def test_db_error_swallowed(self, monkeypatch):
        monkeypatch.setenv("AGENT_MEMORY_ENABLED", "1")
        out = self._call(FakePool(raise_on="execute"))
        assert out["ok"] is False and "error" in out

    def test_empty_run_id_skips(self, monkeypatch):
        monkeypatch.setenv("AGENT_MEMORY_ENABLED", "1")
        out = asyncio.run(mem.record_run_digest(FakePool(), run_id=""))
        assert out["skipped"] is True


# ── distill helpers ──────────────────────────────────────────────────────────


class TestExtractJson:
    def test_fenced(self):
        assert mem.extract_json_array('```json\n[{"a":1}]\n```') == [{"a": 1}]

    def test_bare(self):
        assert mem.extract_json_array('x [{"a":1},{"b":2}] y') == [{"a": 1}, {"b": 2}]

    def test_drops_non_objects(self):
        assert mem.extract_json_array('[1,"x",{"a":1}]') == [{"a": 1}]

    @pytest.mark.parametrize("bad", ["", "no json", "{not valid", None])
    def test_junk(self, bad):
        assert mem.extract_json_array(bad) == []


class TestMemoryId:
    def test_deterministic_and_normalized(self):
        assert mem.memory_id("repo:o/n", "Use pnpm") == mem.memory_id(" repo:o/n ", "use PNPM")
        assert mem.memory_id("global", "A").startswith("mem_")

    def test_distinct(self):
        assert mem.memory_id("global", "A") != mem.memory_id("global", "B")


class TestNormalize:
    def _ok(self):
        return {"scope": "repo:o/n", "kind": "env_quirk", "title": "T", "body": "B",
                "confidence": 0.9, "tags": ["t1"]}

    def test_valid(self):
        m = mem.normalize(self._ok())
        assert m["memory_id"].startswith("mem_") and m["repos"] == ["o/n"] and m["confidence"] == 0.9

    def test_repo_scope_lowercased(self):
        m = mem.normalize({**self._ok(), "scope": "repo:Owner/Repo"})
        assert m["scope"] == "repo:owner/repo" and m["repos"] == ["owner/repo"]

    def test_existing_id_passthrough(self):
        assert mem.normalize({**self._ok(), "memory_id": "mem_abc"})["memory_id"] == "mem_abc"

    def test_bad_kind_dropped(self):
        assert mem.normalize({**self._ok(), "kind": "nonsense"}) is None

    @pytest.mark.parametrize("missing", ["scope", "title", "body"])
    def test_missing_required_dropped(self, missing):
        assert mem.normalize({**self._ok(), missing: ""}) is None

    def test_confidence_clamped(self):
        assert mem.normalize({**self._ok(), "confidence": 5})["confidence"] == 1.0
        assert mem.normalize({**self._ok(), "confidence": -1})["confidence"] == 0.0

    def test_self_supersede_filtered(self):
        m = mem.normalize({**self._ok(), "memory_id": "mem_x", "supersedes": ["mem_x", "mem_y"]})
        assert m["supersedes"] == ["mem_y"]


class TestPromptAndBlobs:
    def _rows(self):
        return [{
            "run_id": "r1", "workflow_name": "example_memory_agent",
            "identifier": "acme/example-app", "repos": ["acme/example-app"],
            "digest_md": "did stuff",
            "learnings_raw": '[{"scope":"global","kind":"pitfall","body":"x"}]',
            "context_pct": 30.0, "outcome": "completed",
        }]

    def test_runs_blob_dedups_learnings(self):
        dup = ('[{"scope":"global","kind":"pitfall","body":"x"},'
               '{"scope":"global","kind":"pitfall","body":"x"},'
               '{"scope":"global","kind":"convention","body":"y"}]')
        blob = mem._runs_blob([{**self._rows()[0], "learnings_raw": dup}])
        assert blob.count("global | pitfall | x") == 1
        assert blob.count("global | convention | y") == 1

    def test_distill_prompt_is_injection_safe(self):
        # _PROMPT embeds a literal JSON example, so it must never be str.format-ed.
        # Data is appended by concatenation, so a literal placeholder token in run
        # text survives verbatim and never spawns a second substitution.
        rows = [dict(self._rows()[0], digest_md="near {existing} and {runs}")]
        prompt = mem.distill_prompt(rows, [])
        assert "acme/example-app" in prompt
        assert prompt.count("none yet") == 1
        assert '{"memory_id"' in prompt
        assert "near {existing} and {runs}" in prompt
        with pytest.raises(KeyError):
            mem._PROMPT.format(runs="x", existing="y")

    def test_digest_message(self):
        msg = mem.digest_message(
            {"runs": 2, "written": 3, "superseded": 1},
            [{"scope": "repo:o/n", "kind": "env_quirk", "title": "Use pnpm"}],
        )
        assert "Agent memory" in msg and "3 written" in msg
        assert "[o/n · env_quirk] Use pnpm" in msg
