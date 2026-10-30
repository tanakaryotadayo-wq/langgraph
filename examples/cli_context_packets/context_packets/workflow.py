"""Adaptive, isolated context branches with complete source/evidence retention."""

from __future__ import annotations

import asyncio
import hashlib
import operator
import re
import sys
from copy import deepcopy
from pathlib import Path
from typing import Annotated, Any, TypedDict
from urllib.parse import unquote, urlparse

from langgraph.graph import END, START, StateGraph
from langgraph.types import Send
from pydantic import BaseModel, ConfigDict, Field

from . import repomap
from .transport import LocalModel, bridge, codex_budget


class Branch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=100)
    query: str = Field(min_length=1, max_length=10000)
    pattern: str = Field(default="", max_length=10000)
    glob: str = Field(default="**/*", max_length=1000)
    fork_turns: str = "none"
    n_final: int = Field(default=25, ge=1, le=200)
    rerank: bool = False
    use_embeddings: bool = False
    tool_calling_only: bool = False


class Plan(BaseModel):
    model_config = ConfigDict(extra="forbid")
    branches: list[Branch] = Field(min_length=1, max_length=16)


class Judgment(BaseModel):
    model_config = ConfigDict(extra="forbid")
    sufficient: bool
    reason: str
    followups: list[Branch] = Field(default_factory=list, max_length=16)


class State(TypedDict, total=False):
    task: str
    root: str
    history: list[dict]
    repo_map: dict
    branches: list[dict]
    iteration: int
    results: Annotated[list[dict], operator.add]
    decisions: Annotated[list[dict], operator.add]
    evidence: list[dict]
    model_context: str
    budget: dict
    continue_search: bool
    stop_reason: str
    answer: Any


def fork_history(history: list[dict], fork_turns: str = "all", **legacy) -> list[dict]:
    """Port of Codex MultiAgentV2 SpawnAgentArgs::fork_mode (source retained).

    none -> fresh; all/positive legacy count -> full history. Returned lists are
    deep copies, so parallel branches cannot mutate their parent or siblings.
    """
    if legacy.get("fork_context") is not None:
        raise ValueError("fork_context is not supported in MultiAgentV2; use fork_turns instead")
    mode = fork_turns.strip().lower() or "all"
    if mode == "none":
        return []
    if mode == "all" or (re.fullmatch(r"\+?[0-9]+", mode) and 0 < int(mode) <= sys.maxsize * 2 + 1):
        return deepcopy(history)
    raise ValueError("fork_turns must be `none` or `all`")


def attest_chunk(chunk: dict, root: Path) -> dict:
    """Bind source observations to real file bytes; AI-generated text is separate."""
    parsed = urlparse(chunk["filepath"])
    if parsed.scheme != "file" or parsed.netloc not in ("", "localhost"):
        raise ValueError("Evidence must be a local file URI")
    file = Path(unquote(parsed.path)).resolve(strict=True)
    if not file.is_relative_to(root):
        raise ValueError("Evidence leaves workspace")
    data = file.read_bytes()
    source_hash = hashlib.sha256(data).hexdigest()
    prior_hash = chunk.get("source_sha256", chunk.get("digest", ""))
    if re.fullmatch(r"[0-9a-f]{64}", prior_hash) and prior_hash != source_hash:
        raise ValueError(f"Evidence source changed since collection: {file}")
    text = data.decode("utf-8")
    start, end = chunk["startLine"], chunk["endLine"]
    if not isinstance(start, int) or not isinstance(end, int):
        raise TypeError("Evidence lines must be integers")
    # Continue's LLM-selected whole-file path uses splitlines() for its endLine,
    # while its content retains the original trailing newline. Support both
    # upstream conventions, but never accept fabricated excerpt contents.
    if start < 0 or end < start or end >= len(text.split("\n")):
        raise ValueError("Evidence line range is invalid")
    expected = "\n".join(text.split("\n")[start : end + 1])
    whole_file = start == 0 and end in (len(text.split("\n")) - 1, len(text.splitlines()) - 1)
    if chunk["content"] != expected and not (whole_file and chunk["content"] == text):
        raise ValueError(f"Evidence content does not match source: {file}")
    result = deepcopy(chunk)
    result["source_sha256"] = source_hash
    result["content_sha256"] = hashlib.sha256(chunk["content"].encode()).hexdigest()
    result["relative_path"] = file.relative_to(root).as_posix()
    return result


def build_graph(settings: dict, model: LocalModel | None):
    max_rounds = settings.get("max_rounds", 3)
    max_branches = settings.get("max_branches", 4)
    if not 1 <= max_rounds <= 20 or not 1 <= max_branches <= 16:
        raise ValueError("max_rounds must be 1..20 and max_branches must be 1..16")

    def validate_plan(value):
        plan = Plan.model_validate(value)
        if len(plan.branches) > max_branches:
            raise ValueError("Plan exceeds configured branch count")
        if len({b.name for b in plan.branches}) != len(plan.branches):
            raise ValueError("Duplicate branch names")
        for branch in plan.branches:
            fork_history([], branch.fork_turns)
            if model is None and (
                branch.rerank or branch.use_embeddings or branch.tool_calling_only
            ):
                raise ValueError("This branch requires a configured local stdio model")
        return [b.model_dump() for b in plan.branches]

    async def prepare(state: State):
        root = Path(state["root"]).resolve(strict=True)
        discovered = await bridge(
            {
                "operation": "qwen",
                "root": str(root),
                "discover_only": True,
                "max_files": settings.get("max_files", 500),
            }
        )
        mapping = await asyncio.to_thread(
            repomap.collect,
            str(root),
            discovered["files"],
            query=state["task"],
            max_tokens=settings.get("map_tokens", 4096),
        )
        mapping["discovery"] = discovered
        return {"root": str(root), "repo_map": mapping, "iteration": 1}

    async def plan(state: State):
        if settings.get("branches"):
            branches = validate_plan({"branches": settings["branches"]})
        elif model:
            result = await model.call(
                "route",
                {
                    "task": state["task"],
                    "repo_map": state["repo_map"]["content"],
                    "schema": Plan.model_json_schema(),
                    "max_branches": max_branches,
                    "history": state.get("history", []),
                },
            )
            branches = validate_plan(result)
        else:
            words = re.findall(r"[\w]+", state["task"], re.UNICODE)
            pattern = "|".join(re.escape(w) for w in words)
            branches = validate_plan(
                {"branches": [{"name": "lexical", "query": state["task"], "pattern": pattern}]}
            )
        return {"branches": branches}

    def dispatch(state: State):
        return [
            Send(
                "collect_branch",
                {
                    **state,
                    "branch": branch,
                    "history": fork_history(state.get("history", []), branch["fork_turns"]),
                },
            )
            for branch in state["branches"]
        ]

    async def collect_branch(state):
        branch = state["branch"]
        source = await bridge(
            {
                "operation": "qwen",
                "root": state["root"],
                "pattern": branch["pattern"],
                "glob": branch["glob"],
                "max_files": settings.get("max_files", 500),
                "context_lines": settings.get("context_lines", 12),
                "max_file_bytes": settings.get("max_file_bytes", 2_000_000),
            }
        )
        root = Path(state["root"])
        qwen = [attest_chunk(c, root) for c in source["chunks"]]
        recent_files = []
        for relative in settings.get("recent_files", []):
            file = (root / relative).resolve(strict=True)
            if not file.is_relative_to(root):
                raise ValueError("Recent file leaves workspace")
            recent_files.append(file.as_uri())
        selected = await bridge(
            {
                "operation": "continue",
                "root_uri": root.as_uri(),
                "branch": branch["name"],
                "query": branch["query"],
                "chunks": qwen,
                "recent_files": recent_files,
                "open_files": recent_files,
                "history": state["history"],
                "repo_map": state["repo_map"]["content"],
                "n_final": branch["n_final"],
                "rerank": branch["rerank"],
                "use_embeddings": branch["use_embeddings"],
                "tool_calling_only": branch["tool_calling_only"],
                "model_enabled": model is not None,
                **(
                    {"bm25_threshold": settings["bm25_threshold"]}
                    if "bm25_threshold" in settings
                    else {}
                ),
            },
            model,
        )
        continue_chunks = []
        observations = []
        documents = [attest_chunk(c, root) for c in selected.get("source_documents", [])]
        for chunk in selected["chunks"]:
            if chunk.get("derivation") == "continue.chunkDocument":
                document = next((d for d in documents if d["filepath"] == chunk["filepath"]), None)
                if document is None:
                    raise ValueError("Unbound derived chunk")
                observations.append(
                    {
                        **chunk,
                        "observation_type": "source_derived_chunk",
                        "source_sha256": document["source_sha256"],
                    }
                )
            elif chunk["startLine"] == -1 and selected.get("tool_outputs"):
                raw_items = [item for event in selected["tool_outputs"] for item in event["items"]]
                if not any(item["content"] == chunk["content"] for item in raw_items):
                    raise ValueError("Unbound tool observation")
                observations.append({**chunk, "observation_type": "executed_read_tool"})
            else:
                continue_chunks.append(attest_chunk(chunk, root))
        return {
            "results": [
                {
                    "iteration": state["iteration"],
                    "branch": branch,
                    "history": state["history"],
                    "qwen": {**source, "chunks": qwen},
                    "continue": {
                        **selected,
                        "raw_chunks": selected["chunks"],
                        "chunks": continue_chunks + documents,
                        "observations": observations,
                    },
                }
            ]
        }

    async def merge(state: State):
        unique = {}
        selected_keys = set()
        # Keep the complete observations from every round. Selection below only
        # changes ordering in the model view; it never deletes source evidence.
        for result in state["results"]:
            for producer in ("continue", "qwen"):
                for chunk in result[producer]["chunks"]:
                    key = (
                        chunk["filepath"],
                        chunk["startLine"],
                        chunk["endLine"],
                        chunk["content_sha256"],
                    )
                    item = unique.setdefault(key, {**chunk, "origins": []})
                    origin = {
                        "branch": result["branch"]["name"],
                        "packet": producer,
                        "iteration": result["iteration"],
                    }
                    if origin not in item["origins"]:
                        item["origins"].append(origin)
                    if producer == "continue":
                        selected_keys.add(key)
        ordered = sorted(unique, key=lambda key: (key not in selected_keys, key))
        evidence = [unique[key] for key in ordered]
        text = (
            state["repo_map"]["content"]
            + "\n\n"
            + "\n\n".join(
                f"{c['relative_path']}:{c['startLine'] + 1}-{c['endLine'] + 1}\n{c['content']}"
                for c in evidence
            )
        )
        for result in state["results"]:
            for item in result["continue"].get("observations", []):
                text += "\n\n" + item["observation_type"] + ":\n" + item["content"]
        budget = await codex_budget(text, settings.get("context_tokens", 16000))
        return {"evidence": evidence, "model_context": budget["text"], "budget": budget}

    async def judge(state: State):
        if model is None:
            judgment = Judgment(
                sufficient=False, reason="Offline collection only; no inference claimed"
            )
            return {
                "continue_search": False,
                "stop_reason": "offline_collection",
                "decisions": [{"iteration": state["iteration"], **judgment.model_dump()}],
            }
        response = await model.call(
            "judge",
            {
                "task": state["task"],
                "context": state["model_context"],
                "branches": state["branches"],
                "iteration": state["iteration"],
                "budget": state["budget"],
                "schema": Judgment.model_json_schema(),
            },
        )
        judgment = Judgment.model_validate(response)
        again = (
            not judgment.sufficient and bool(judgment.followups) and state["iteration"] < max_rounds
        )
        update = {
            "continue_search": again,
            "decisions": [{"iteration": state["iteration"], **judgment.model_dump()}],
        }
        if again:
            update.update(
                branches=validate_plan({"branches": [b.model_dump() for b in judgment.followups]}),
                iteration=state["iteration"] + 1,
            )
        else:
            update["stop_reason"] = (
                "sufficient"
                if judgment.sufficient
                else "round_limit"
                if state["iteration"] >= max_rounds
                else "no_followups"
            )
        return update

    async def finish(state: State):
        answer = None
        if model and settings.get("synthesize", True):
            answer = await model.call(
                "synthesize",
                {
                    "task": state["task"],
                    "context": state["model_context"],
                    "sources": [
                        {
                            "path": c["relative_path"],
                            "start": c["startLine"] + 1,
                            "end": c["endLine"] + 1,
                        }
                        for c in state["evidence"]
                    ],
                    "stop_reason": state["stop_reason"],
                    "output_contract": "JSON object with answer, citations, limitations",
                },
            )
        return {"answer": answer}

    graph = StateGraph(State)
    for name, node in (
        ("prepare", prepare),
        ("plan", plan),
        ("collect_branch", collect_branch),
        ("merge", merge),
        ("judge", judge),
        ("finish", finish),
    ):
        graph.add_node(name, node)
    graph.add_edge(START, "prepare")
    graph.add_edge("prepare", "plan")
    graph.add_conditional_edges("plan", dispatch, ["collect_branch"])
    graph.add_edge("collect_branch", "merge")
    graph.add_edge("merge", "judge")
    graph.add_conditional_edges(
        "judge",
        lambda state: "search" if state["continue_search"] else "finish",
        {"search": "dispatch_again", "finish": "finish"},
    )
    graph.add_node("dispatch_again", lambda state: {})
    graph.add_conditional_edges("dispatch_again", dispatch, ["collect_branch"])
    graph.add_edge("finish", END)
    return graph.compile()
