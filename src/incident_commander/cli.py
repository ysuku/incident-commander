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


def cmd_diagnose(args) -> None:
    """Run the agent on one case. The step-by-step log streams to the terminal
    (stderr) and the brief prints as JSON (stdout). Both are also saved under
    evals/runs/ so every experiment is kept."""
    import json
    import logging
    import os
    import sys
    from datetime import datetime

    from . import loop
    from .config import REPO_ROOT

    case = loaders.load_case(args.case, args.system)
    if args.model == "mock":
        from .model import MockModel
        model, model_name = MockModel(), "mock"
    else:
        from .model import OpenRouterClient
        model = OpenRouterClient(None if args.model == "env" else args.model)
        model_name = model.model.split("/")[-1]

    stem = f"{datetime.now():%Y-%m-%d_%H%M}_{model_name}_{case.case_id.replace('/', '_')}"
    run_dir = REPO_ROOT / "evals" / "runs"
    run_dir.mkdir(parents=True, exist_ok=True)

    logger = logging.getLogger("incident_commander")
    logger.setLevel(logging.INFO)
    fmt = logging.Formatter("%(asctime)s %(message)s", datefmt="%H:%M:%S")
    for handler in (logging.StreamHandler(sys.stderr),
                    logging.FileHandler(run_dir / f"{stem}.log", encoding="utf-8")):
        handler.setFormatter(fmt)
        logger.addHandler(handler)
    logger.info("model=%s | max_tokens=%s", model_name,
                "n/a" if model_name == "mock" else os.environ.get("IC_MAX_TOKENS", "4096"))

    brief = loop.diagnose(case, model)
    out = json.dumps(brief, indent=2, default=str)
    (run_dir / f"{stem}.json").write_text(out, encoding="utf-8")
    print(out)
    logger.info("saved evals/runs/%s.json and .log", stem)


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

    dp = sub.add_parser("diagnose", help="Run the agent on one case")
    dp.add_argument("--case", required=True)
    dp.add_argument("--system", default="online-boutique")
    dp.add_argument("--model", default="env",
                    help="'mock' (keyless demo), or 'env' to use .env OPENAI_MODEL")
    dp.set_defaults(fn=cmd_diagnose)

    args = p.parse_args()
    fn = getattr(args, "fn", None)
    if fn is None:
        raise SystemExit(f"'{args.cmd}' not implemented yet — see DESIGN_LOG.md for the build order.")
    fn(args)


if __name__ == "__main__":
    main()
