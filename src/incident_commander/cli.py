"""CLI entry points.

  python -m incident_commander.cli list --system online-boutique
  python -m incident_commander.cli baseline --kind plain --case <id>
  python -m incident_commander.cli diagnose --case <id>        (agent; M2+)
"""
import argparse
from datetime import datetime, timezone

from . import loaders


def cmd_list(args) -> None:
    cases = loaders.list_cases(args.system)
    print(f"{len(cases)} cases in {args.system}:")
    for c in cases:
        t = datetime.fromtimestamp(c.inject_time, tz=timezone.utc).strftime("%Y-%m-%d %H:%M")
        print(f"  {c.case_id:40s} injected {t}Z")


def main() -> None:
    p = argparse.ArgumentParser(prog="incident_commander")
    sub = p.add_subparsers(dest="cmd", required=True)

    lp = sub.add_parser("list", help="List downloaded failure cases")
    lp.add_argument("--system", default="online-boutique")
    lp.set_defaults(fn=cmd_list)

    bp = sub.add_parser("baseline", help="Run a non-agent baseline on one case")
    bp.add_argument("--kind", choices=["plain", "cot"], required=True)
    bp.add_argument("--case", required=True)
    bp.add_argument("--system", default="online-boutique")

    dp = sub.add_parser("diagnose", help="Run the agent on one case (M2+)")
    dp.add_argument("--case", required=True)
    dp.add_argument("--system", default="online-boutique")

    args = p.parse_args()
    fn = getattr(args, "fn", None)
    if fn is None:
        raise SystemExit(f"'{args.cmd}' not implemented yet — see DESIGN_LOG.md for the build order.")
    fn(args)


if __name__ == "__main__":
    main()
