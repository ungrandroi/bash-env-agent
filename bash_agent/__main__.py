import argparse
import importlib
import json
from pathlib import Path
from .agent import run_agent
from .client import DEMO_TASKS, HTTPClient, ScriptedClient
from .environment import BashEnvironment
from .memory import IdentityMemory, RecentTurnsMemory


def main():
    parser = argparse.ArgumentParser(description="Minimal Bash agent with replaceable memory")
    parser.add_argument("--backend", choices=["docker", "local"], default="docker",
                        help="local executes on this machine and is NOT a sandbox")
    parser.add_argument("--image", default="bash:5.2")
    parser.add_argument("--client", choices=["scripted", "http"], default="scripted")
    parser.add_argument("--base-url", default="http://127.0.0.1:11434/v1")
    parser.add_argument("--model")
    parser.add_argument("--memory", default="identity", help="identity, recent, or package.module:ClassName")
    parser.add_argument("--recent-turns", type=int, default=1)
    parser.add_argument("--task", action="append", help="Repeat to add user turns; HTTP client only")
    parser.add_argument("--max-steps", type=int, default=8, help="Model calls per user turn")
    parser.add_argument("--command-timeout", type=float, default=5)
    parser.add_argument("--request-timeout", type=float, default=120)
    parser.add_argument("--output", type=Path, default=Path("runs/demo.json"))
    args = parser.parse_args()
    if args.client == "http" and not args.model:
        parser.error("--model is required for HTTP")
    if args.client == "scripted" and args.task:
        parser.error("The scripted client only supports the built-in demo tasks")
    if args.output.exists():
        parser.error("Output exists; use another path to preserve previous runs")
    if min(args.max_steps, args.command_timeout, args.request_timeout, args.recent_turns) <= 0:
        parser.error("Step limits and timeouts must be positive")
    if args.memory == "identity":
        memory = IdentityMemory()
    elif args.memory == "recent":
        memory = RecentTurnsMemory(args.recent_turns)
    else:
        module, separator, name = args.memory.partition(":")
        if not separator:
            parser.error("Custom memory must be package.module:ClassName")
        memory = getattr(importlib.import_module(module), name)()
    client = (ScriptedClient() if args.client == "scripted" else
              HTTPClient(args.base_url, args.model, timeout=args.request_timeout))
    with BashEnvironment(args.backend, image=args.image, timeout=args.command_timeout) as env:
        trace = run_agent(args.task or DEMO_TASKS, env, client, memory, args.max_steps)
    trace["config"] = {"client": args.client, "model": args.model, "memory": args.memory,
                       "base_url": args.base_url if args.client == "http" else None,
                       "image": args.image if args.backend == "docker" else None,
                       "max_steps": args.max_steps, "command_timeout": args.command_timeout,
                       "request_timeout": args.request_timeout}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(trace, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    for turn in trace["turns"]:
        print(f"Turn {turn['turn']+1}: {turn['status']} | {turn['answer'] or ''}")
    print(f"Trace: {args.output}")
    return int(any(turn["status"] != "completed" for turn in trace["turns"]))


if __name__ == "__main__":
    raise SystemExit(main())
