"""CLI entry point; JSON stdin/stdout is also available for nesting this workflow."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

from .provenance import verify_packets, write_run
from .transport import LocalModel
from .workflow import build_graph


async def run(request: dict) -> dict:
    settings = request.get("settings", {})
    model_config = request.get("model")
    model = LocalModel(model_config) if model_config else None
    result = await build_graph(settings, model).ainvoke(
        {
            "task": request["task"],
            "root": request["root"],
            "history": request.get("history", []),
            "results": [],
            "decisions": [],
        },
        config={
            "recursion_limit": settings.get("max_rounds", 3) * 8 + 10,
            "max_concurrency": settings.get("max_branches", 4),
        },
    )
    result["inference"] = "local-stdio" if model else "not-run"
    result["model_calls"] = model.history if model else []
    result["artifact"] = str(write_run(result))
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("task", nargs="?")
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--config", type=Path)
    parser.add_argument("--stdio", action="store_true", help="One workflow JSON request per line")
    parser.add_argument("--verify", action="store_true", help="Verify all vendored source bytes")
    args = parser.parse_args()
    if args.verify:
        print(json.dumps(verify_packets(), indent=2))
        return
    if args.stdio:
        for line in sys.stdin:
            try:
                print(
                    json.dumps({"result": asyncio.run(run(json.loads(line)))}, ensure_ascii=False),
                    flush=True,
                )
            except (
                ValueError,
                TypeError,
                RuntimeError,
                OSError,
                KeyError,
                ExceptionGroup,
            ) as error:
                print(json.dumps({"error": str(error)}, ensure_ascii=False), flush=True)
        return
    if not args.task:
        parser.error("provide a task or --stdio")
    request = json.loads(args.config.read_text()) if args.config else {}
    request.update(task=args.task, root=str(args.root.resolve(strict=True)))
    print(json.dumps(asyncio.run(run(request)), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
