from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from context_packets import repomap
from context_packets.cli import run
from context_packets.provenance import write_run
from context_packets.transport import (
    ROOT,
    LocalModel,
    TransportError,
    bridge,
    codex_budget,
    execute,
)
from context_packets.workflow import attest_chunk, fork_history


@pytest.fixture
def workspace(tmp_path):
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    (tmp_path / "engine.py").write_text(
        "class Engine:\n    def run(self):\n        return Policy()\n"
    )
    (tmp_path / "policy.py").write_text(
        "class Policy:\n    def decide(self):\n        return True\n"
    )
    (tmp_path / ".gitignore").write_text("ignored.py\n")
    (tmp_path / ".qwenignore").write_text("private.py\n")
    (tmp_path / "ignored.py").write_text("class EngineIgnored: pass\n")
    (tmp_path / "private.py").write_text("class EnginePrivate: pass\n")
    return tmp_path


async def test_original_qwen_search_ignore_and_source_binding(workspace):
    result = await bridge({"operation": "qwen", "root": str(workspace), "pattern": "Engine"})
    assert result["chunks"]
    assert not any(Path(p).name in ("ignored.py", "private.py") for p in result["files"])
    chunk = attest_chunk(result["chunks"][0], workspace)
    assert chunk["relative_path"] == "engine.py"
    assert len(chunk["source_sha256"]) == 64
    assert result["recovery"]["selectionMode"] == "system"


async def test_no_matches_is_success_not_partial(workspace):
    result = await bridge(
        {"operation": "qwen", "root": str(workspace), "pattern": "never_matching_marker"}
    )
    assert result["chunks"] == []


async def test_glob_does_not_read_symlink_outside_root(workspace, tmp_path_factory):
    external = tmp_path_factory.mktemp("external") / "outside.py"
    external.write_text("class EngineOutside: pass")
    (workspace / "escape.py").symlink_to(external)
    result = await bridge({"operation": "qwen", "root": str(workspace), "pattern": "EngineOutside"})
    assert result["chunks"] == []
    assert any(entry["reason"] == "outside_workspace" for entry in result["omitted"])


def test_original_aider_ast_map(workspace):
    result = repomap.collect(
        str(workspace),
        [str(workspace / "engine.py"), str(workspace / "policy.py")],
        query="Engine Policy",
    )
    assert "Engine" in result["content"]
    assert "Policy" in result["content"]
    assert not list(workspace.glob(".aider*"))


async def test_original_continue_fts_pipeline(workspace):
    qwen = await bridge({"operation": "qwen", "root": str(workspace), "pattern": "Engine|Policy"})
    result = await bridge(
        {
            "operation": "continue",
            "root_uri": workspace.as_uri(),
            "query": "Engine",
            "chunks": qwen["chunks"],
            "n_final": 20,
            "model_enabled": False,
            "bm25_threshold": 0,
        }
    )
    assert result["chunks"]
    assert any("Engine" in c["content"] for c in result["chunks"])


async def test_real_rust_utf8_budget():
    text = "先頭🙂" + "日本語の文脈" * 200 + "末尾🚀"
    value = await codex_budget(text, 100)
    assert value["changed"]
    assert value["text"].startswith("先頭🙂") and value["text"].endswith("末尾🚀")
    assert "truncated" in value["text"]
    assert (await codex_budget("短文", 100))["text"] == "短文"


@pytest.mark.parametrize("mode", ["all", "ALL", "  all  ", "1", "+1", "0002", ""])
def test_codex_fork_full_history_isolated(mode):
    parent = [{"content": {"nested": ["parent"]}}]
    child = fork_history(parent, mode)
    child[0]["content"]["nested"].append("child")
    assert parent == [{"content": {"nested": ["parent"]}}]


@pytest.mark.parametrize("mode", ["0", "-1", "1.2", "unknown", "９", "18446744073709551616"])
def test_codex_invalid_fork(mode):
    with pytest.raises(ValueError):
        fork_history([], mode)


def test_fresh_fork_and_legacy_rejection():
    assert fork_history([{"parent": True}], "none") == []
    with pytest.raises(ValueError):
        fork_history([], "all", fork_context=False)


def test_fabricated_source_is_rejected(workspace):
    with pytest.raises(ValueError, match="does not match source"):
        attest_chunk(
            {
                "filepath": (workspace / "engine.py").as_uri(),
                "startLine": 0,
                "endLine": 1,
                "content": "AI invented implementation",
            },
            workspace,
        )


async def test_stdio_failure_and_timeout():
    with pytest.raises(TransportError, match="exited 7"):
        await execute([sys.executable, "-c", "raise SystemExit(7)"], b"")
    with pytest.raises(TransportError, match="timeout"):
        await execute([sys.executable, "-c", "import time;time.sleep(10)"], b"", timeout=0.05)


async def test_model_fixture_full_graph_parallel_followup_and_no_evidence_loss(workspace):
    result = await run(
        {
            "task": "Find Engine policy",
            "root": str(workspace),
            "history": [{"role": "user", "content": "parent-history"}],
            "model": {
                "command": [sys.executable, str(ROOT / "tests/fixtures/model.py")],
                "format": "json",
            },
            "settings": {"max_rounds": 2, "context_tokens": 20},
        }
    )
    assert result["iteration"] == 2
    assert result["stop_reason"] == "sufficient"
    assert len(result["results"]) == 3
    assert result["answer"]["answer"] == "fixture-response"
    assert result["budget"]["changed"]
    assert any("class Engine" in c["content"] for c in result["evidence"])
    assert any("class Policy" in c["content"] for c in result["evidence"])
    assert all(r["history"] == [] for r in result["results"])
    operations = {c["request"]["operation"] for c in result["model_calls"]}
    assert {"route", "chat", "rerank", "embed", "judge", "synthesize"} <= operations
    artifact = json.loads(Path(result["artifact"]).read_text())
    assert artifact["evidence"] == result["evidence"]


async def test_offline_mode_does_not_claim_inference(workspace):
    result = await run(
        {"task": "Engine", "root": str(workspace), "settings": {"context_tokens": 1000}}
    )
    assert result["inference"] == "not-run"
    assert result["stop_reason"] == "offline_collection"
    assert result["evidence"] and result["answer"] is None


async def test_all_six_read_tools_execute_locally(workspace):
    result = await run(
        {
            "task": "Engine",
            "root": str(workspace),
            "model": {
                "command": [sys.executable, str(ROOT / "tests/fixtures/model.py")],
                "format": "json",
            },
            "settings": {
                "max_rounds": 1,
                "branches": [
                    {
                        "name": "tools",
                        "query": "Engine",
                        "pattern": "Engine",
                        "tool_calling_only": True,
                    }
                ],
            },
        }
    )
    selected = result["results"][0]["continue"]
    assert len(selected["tool_outputs"]) == 6
    assert selected["observations"]
    assert "class Engine" in selected["tool_outputs"][0]["items"][0]["content"]
    assert result["stop_reason"] == "round_limit"


async def test_real_mcp_stdio_handshake_and_tool_protocol():
    model = LocalModel(
        {"command": [sys.executable, str(ROOT / "tests/fixtures/mcp_model.py")], "format": "mcp"}
    )
    result = await model.call("route", {"task": "protocol"})
    assert len(result["branches"]) == 2
    assert model.history[0]["response"] == result


async def test_plain_text_cli_transport():
    model = LocalModel(
        {
            "command": [sys.executable, str(ROOT / "tests/fixtures/model.py"), "--text"],
            "format": "text",
        }
    )
    route = await model.call("route", {"task": "protocol"})
    assert len(route["branches"]) == 2
    chat = await model.call("chat", {"messages": [{"role": "user", "content": "protocol"}]})
    assert "<results>" in chat and "engine.py" in chat


async def test_source_edits_invalidate_captured_evidence(workspace):
    result = await bridge({"operation": "qwen", "root": str(workspace), "pattern": "Engine"})
    (workspace / "engine.py").write_text("class Engine:\n    pass\n")
    with pytest.raises(ValueError, match="changed since collection"):
        attest_chunk(result["chunks"][0], workspace)


async def test_original_continue_code_chunker_and_parser(workspace):
    contents = (workspace / "engine.py").read_text()
    result = await bridge(
        {
            "operation": "continue_chunk",
            "filepath": (workspace / "engine.py").as_uri(),
            "contents": contents,
            "maxChunkSize": 384,
            "digest": "fixture",
        }
    )
    assert result["parser_available"]
    assert result["chunks"] and "Engine" in result["chunks"][0]["content"]


async def test_recent_files_and_inherited_history_reach_original_pipeline(workspace):
    result = await run(
        {
            "task": "Engine",
            "root": str(workspace),
            "history": [{"role": "user", "content": "PARENT_SENTINEL"}],
            "model": {
                "command": [sys.executable, str(ROOT / "tests/fixtures/model.py")],
                "format": "json",
            },
            "settings": {
                "max_rounds": 1,
                "recent_files": ["policy.py"],
                "branches": [
                    {"name": "inherit", "query": "Engine", "pattern": "Engine", "fork_turns": "all"}
                ],
            },
        }
    )
    calls = [c for c in result["model_calls"] if c["request"]["operation"] == "chat"]
    assert any(c["request"]["messages"][0]["content"] == "PARENT_SENTINEL" for c in calls)
    branch = result["results"][0]["continue"]
    assert any("class Policy" in d["content"] for d in branch["source_documents"])
    assert any(c.get("derivation") == "continue.chunkDocument" for c in branch["raw_chunks"])


def test_content_addressed_artifact_idempotent(tmp_path):
    first = write_run({"evidence": ["original"]}, tmp_path)
    assert write_run({"evidence": ["original"]}, tmp_path) == first
    assert write_run({"evidence": ["changed"]}, tmp_path) != first
