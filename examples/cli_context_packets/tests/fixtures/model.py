"""Deterministic protocol test fixture. This is not a local AI implementation."""

import json
import sys


def generate(request):
    operation = request["operation"]
    if operation == "route":
        return {
            "branches": [
                {"name": "engine", "query": "engine", "pattern": "Engine", "rerank": True},
                {"name": "policy", "query": "policy", "pattern": "Policy", "use_embeddings": True},
            ]
        }
    if operation == "rerank":
        return [float(i) for i in range(len(request["chunks"]))]
    if operation == "embed":
        return [[1.0, float(len(text) % 7) + 1.0] for text in request["texts"]]
    if operation == "chat":
        if "Available tools:" in request["messages"][0]["content"]:
            return json.dumps(
                {
                    "tools": [
                        {"name": "read_file", "args": {"filepath": "engine.py"}},
                        {"name": "grep_search", "args": {"query": "Engine"}},
                        {"name": "file_glob_search", "args": {"pattern": "*.py"}},
                        {"name": "ls", "args": {"dirPath": "."}},
                        {"name": "view_repo_map", "args": {}},
                        {"name": "view_subdirectory", "args": {"directory_path": "."}},
                    ]
                }
            )
        return "<reasoning>Protocol fixture.</reasoning>\n<results>\nengine.py\n</results>"
    if operation == "judge":
        if request["iteration"] == 1:
            return {
                "sufficient": False,
                "reason": "Exercise follow-up branch",
                "followups": [
                    {"name": "followup", "query": "engine policy", "pattern": "Engine|Policy"}
                ],
            }
        return {"sufficient": True, "reason": "Protocol fixture finished", "followups": []}
    if operation == "synthesize":
        return {
            "answer": "fixture-response",
            "citations": request["sources"],
            "limitations": ["test fixture, not inference"],
        }
    raise ValueError(operation)


if __name__ == "__main__":
    payload = sys.stdin.read()
    if "--text" in sys.argv:
        request = json.loads(payload.split("\n", 1)[1])
        response = generate(request)
        print(response if request["operation"] == "chat" else json.dumps(response))
    else:
        request = json.loads(payload)
        print(json.dumps({"result": generate(request)}))
