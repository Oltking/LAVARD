"""LAVARD CLI — a thin stdlib entrypoint over the conductor.

    python cli.py run "get the current BTC price"
    python cli.py run "research competitors then draft a brief" --demo
    python cli.py report <job_id>

Offline by default (no executor wired → escrow/room simulate); the API process wires the real
TheHouse executor. Kept dependency-free so it runs with nothing installed.
"""

from __future__ import annotations

import argparse
import json
import sys


def _run(args: argparse.Namespace) -> int:
    from core.conductor import run_job

    result = run_job(args.goal, owner_id=args.owner, demo=args.demo,
                     auto_signoff=args.auto_signoff, preference=args.preference)
    print(json.dumps(result.to_dict(), indent=2))
    return 0


def _report(args: argparse.Namespace) -> int:
    from core.governance import build_report

    print(json.dumps(build_report(args.job_id), indent=2))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="lavard", description="LAVARD orchestration CLI")
    sub = parser.add_subparsers(dest="command", required=True)

    run = sub.add_parser("run", help="Run a goal end-to-end via the conductor.")
    run.add_argument("goal")
    run.add_argument("--owner", default="default-owner")
    run.add_argument("--demo", action="store_true", help="Use the scripted demo room scenario.")
    run.add_argument("--auto-signoff", action="store_true",
                     help="Release escrow automatically (default: stop at sign-off).")
    run.add_argument("--preference", default="balanced",
                     choices=["cheapest", "fastest", "smartest", "balanced"],
                     help="Agent-selection objective (default: balanced).")
    run.set_defaults(func=_run)

    rep = sub.add_parser("report", help="Print the per-job report + audit log.")
    rep.add_argument("job_id")
    rep.set_defaults(func=_report)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
