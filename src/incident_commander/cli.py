"""CLI entry points.

  python -m incident_commander.cli list --system online-boutique
  python -m incident_commander.cli baseline --kind plain --case <id>
  python -m incident_commander.cli diagnose --case <id>        (agent; M2+)
  python -m incident_commander.cli eval --model claude-opus-5-5 --runs 1 --max-usd 20
  python -m incident_commander.cli rescore <results_dir>       (re-score saved briefs; no model calls)
  add --tot model|heuristic to diagnose/eval for Tree-of-Thought (Checkpoint 4.1)
  add --arch graph to diagnose/eval for the multi-agent LangGraph architecture (Checkpoint 5.1)
  add --hide metrics|logs|traces to diagnose/eval to withhold one telemetry kind (6.1 robustness)
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


def make_model(name: str):
    """(model, short name). 'mock'; 'claude-*' -> Anthropic API; 'env' or any
    other id -> the OpenAI-compatible client (.env OPENAI_MODEL for 'env')."""
    if name == "mock":
        from .model import MockModel
        return MockModel(), "mock"
    if name.startswith("claude-"):
        from .model import AnthropicClient
        return AnthropicClient(name), name
    from .model import OpenRouterClient
    model = OpenRouterClient(None if name == "env" else name)
    return model, model.model.split("/")[-1]


def _log_to(path) -> None:
    import logging
    import sys
    logger = logging.getLogger("incident_commander")
    logger.setLevel(logging.INFO)
    fmt = logging.Formatter("%(asctime)s %(message)s", datefmt="%H:%M:%S")
    for handler in (logging.StreamHandler(sys.stderr), logging.FileHandler(path, encoding="utf-8")):
        handler.setFormatter(fmt)
        logger.addHandler(handler)


def default_out_name(model_name: str, tot: str | None, arch: str, system: str = "online-boutique",
                     hide: str | None = None, day: str | None = None) -> str:
    """The results folder name when --out is not given. Runs resume by folder, so
    everything that makes a run a different experiment is in the name: another
    system or a hidden telemetry kind must not land in (and mix with) an ordinary
    Online Boutique folder. The ordinary name is unchanged."""
    from .config import DEV_SYSTEM
    return (f"{day or f'{datetime.now():%Y-%m-%d}'}_{model_name}"
            + (f"_{system}" if system != DEV_SYSTEM else "")
            + (f"_tot-{tot}" if tot else "") + ("_graph" if arch == "graph" else "")
            + (f"_hide-{hide}" if hide else ""))


def _shown(path, root) -> str:
    """A path for the log: relative to the repo when it is inside it, else as given
    (--out may be an absolute folder elsewhere, e.g. a scratch directory)."""
    try:
        return str(path.relative_to(root))
    except ValueError:
        return str(path)


def cmd_eval(args) -> None:
    """Run the agent over the dev set and score it against the labels."""
    from fnmatch import fnmatch

    from . import evaluate
    from .config import REPO_ROOT

    # the whole run at this --runs; --cases and --limit only pick a part of it
    whole = [c for c in loaders.list_cases(args.system)
             if not args.runs or c.run in args.runs.split(",")]
    cases = [c for c in whole if not args.cases or fnmatch(c.case_id, args.cases)]
    if args.limit:
        cases = cases[: args.limit]
    _, model_name = make_model(args.model)
    tot = args.tot or ("model" if args.arch == "graph" else None)
    hide = (args.hide,) if args.hide else ()
    default = default_out_name(model_name, tot, args.arch, args.system, args.hide)
    out = REPO_ROOT / "evals" / "results" / (args.out or default)
    out.mkdir(parents=True, exist_ok=True)
    _log_to(out / "eval.log")
    import logging
    logging.getLogger("incident_commander").info(
        "EVAL model=%s | arch=%s | tot=%s | system=%s | hide=%s | %d cases | out=%s | cap=%s",
        model_name, args.arch, tot or "off", args.system, args.hide or "none", len(cases),
        _shown(out, REPO_ROOT), f"${args.max_usd}" if args.max_usd else "none")
    evaluate.run_eval(cases, lambda: make_model(args.model)[0], out, args.max_usd, tot=tot, arch=args.arch,
                      hide=hide, whole_run=whole)
    print((out / "summary.md").read_text())


def cmd_rescore(args) -> None:
    """Rebuild results.csv and summary.md from saved briefs. Builds no model, so
    it is free; writes to a new sibling folder unless --out says otherwise."""
    from pathlib import Path

    from . import evaluate
    from .config import REPO_ROOT

    src = Path(args.results_dir)
    if not src.is_dir():  # a bare folder name means one under evals/results/
        src = REPO_ROOT / "evals" / "results" / args.results_dir
    if not src.is_dir():
        raise SystemExit(f"no results folder {args.results_dir!r}")
    src = src.resolve()
    out = Path(args.out) if args.out else src.with_name(src.name + "_rescored")
    if out.resolve() == src:
        raise SystemExit("rescore never overwrites the folder it reads; pick another --out")
    rows = evaluate.rescore(loaders.list_cases(args.system), src, out)
    print(f"{len(rows)} saved briefs re-scored from {src} into {out}\n")
    print((out / "summary.md").read_text())


def cmd_diagnose(args) -> None:
    """Run the agent on one case. The step-by-step log streams to the terminal
    (stderr) and the brief prints as JSON (stdout). Both are also saved under
    evals/runs/ so every experiment is kept."""
    import json
    import logging
    import os

    from . import loop
    from .config import REPO_ROOT

    case = loaders.load_case(args.case, args.system)
    model, model_name = make_model(args.model)

    tot = args.tot or ("model" if args.arch == "graph" else None)
    stem = (f"{datetime.now():%Y-%m-%d_%H%M}_{model_name}{'_tot' if tot else ''}"
            f"{'_graph' if args.arch == 'graph' else ''}{f'_hide-{args.hide}' if args.hide else ''}"
            f"_{case.case_id.replace('/', '_')}")
    run_dir = REPO_ROOT / "evals" / "runs"
    run_dir.mkdir(parents=True, exist_ok=True)

    _log_to(run_dir / f"{stem}.log")
    logging.getLogger("incident_commander").info("model=%s | arch=%s | max_tokens=%s", model_name, args.arch,
                "n/a" if model_name == "mock" else os.environ.get("IC_MAX_TOKENS", "4096"))

    from . import evaluate
    brief = evaluate.diagnose(case, model, tot=tot, arch=args.arch, hide=(args.hide,) if args.hide else ())
    out = json.dumps(brief, indent=2, default=str)
    (run_dir / f"{stem}.json").write_text(out, encoding="utf-8")
    print(out)
    logging.getLogger("incident_commander").info("saved evals/runs/%s.json and .log", stem)


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
                    help="'mock' (keyless demo), 'claude-opus-5-5' etc. (Anthropic API), "
                         "or 'env' to use .env OPENAI_MODEL")
    dp.add_argument("--tot", choices=["model", "heuristic"],
                    help="Tree-of-Thought on ambiguity (4.1): 'model' = LLM thought generator, "
                         "'heuristic' = code picks each test (no extra model calls)")
    dp.add_argument("--arch", choices=["linear", "graph"], default="linear",
                    help="'linear' = the single-agent loop (2.1-4.1); 'graph' = the LangGraph "
                         "multi-agent architecture (5.1: Commander, Investigators, Fix Advisor; "
                         "ToT defaults to 'model')")
    dp.add_argument("--hide", choices=["metrics", "logs", "traces"],
                    help="robustness ablation (6.1): withhold one telemetry kind from every role; "
                         "the two-kinds gate is NOT relaxed, so expect more escalations")
    dp.set_defaults(fn=cmd_diagnose)

    ep = sub.add_parser("eval", help="Run the agent over many cases and score them")
    ep.add_argument("--model", default="env", help="same choices as diagnose --model")
    ep.add_argument("--system", default="online-boutique")
    ep.add_argument("--runs", help="comma list of repetitions to include, e.g. '1' or '1,2,3'")
    ep.add_argument("--cases", help="glob on case id, e.g. 'checkout*'")
    ep.add_argument("--limit", type=int, help="first N cases only (smoke test)")
    ep.add_argument("--max-usd", type=float, help="stop starting new cases past this spend")
    ep.add_argument("--out", help="results folder name under evals/results/, or an absolute path "
                                  "(reuse to resume)")
    ep.add_argument("--tot", choices=["model", "heuristic"],
                    help="Tree-of-Thought on ambiguity (4.1): 'model' = LLM thought generator, "
                         "'heuristic' = code picks each test (no extra model calls)")
    ep.add_argument("--arch", choices=["linear", "graph"], default="linear",
                    help="same choices as diagnose --arch")
    ep.add_argument("--hide", choices=["metrics", "logs", "traces"],
                    help="robustness ablation (6.1): withhold one telemetry kind from every role; "
                         "the two-kinds gate is NOT relaxed, so expect more escalations")
    ep.set_defaults(fn=cmd_eval)

    rp = sub.add_parser("rescore", help="Re-score saved briefs in a results folder (no model calls)")
    rp.add_argument("results_dir", help="path to a results folder, or its name under evals/results/")
    rp.add_argument("--out", help="folder to write results.csv and summary.md to "
                                  "(default: a new sibling '<results_dir>_rescored')")
    rp.add_argument("--system", default="online-boutique")
    rp.set_defaults(fn=cmd_rescore)

    args = p.parse_args()
    fn = getattr(args, "fn", None)
    if fn is None:
        raise SystemExit(f"'{args.cmd}' not implemented yet — see DESIGN_LOG.md for the build order.")
    fn(args)


if __name__ == "__main__":
    main()
