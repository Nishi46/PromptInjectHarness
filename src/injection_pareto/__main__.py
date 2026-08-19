import argparse

from injection_pareto.adaptive import (
    ADAPTIVE_ROUND_BUDGET,
    expand_adaptive_trial_points,
    run_adaptive_sweep,
)
from injection_pareto.config import expand_run_specs, load_config
from injection_pareto.sweep import run_sweep


def main() -> None:
    parser = argparse.ArgumentParser(prog="injection_pareto")
    subparsers = parser.add_subparsers(dest="command", required=True)

    run_parser = subparsers.add_parser("run", help="Run an experiment config")
    run_parser.add_argument("config", help="Path to a YAML experiment config")
    run_parser.add_argument(
        "--concurrency", type=int, default=4, help="Max concurrent episodes (default: 4)"
    )
    run_parser.add_argument(
        "--no-cache", action="store_true", help="Bypass the response cache for this run"
    )
    run_parser.add_argument(
        "--no-progress", action="store_true", help="Disable the progress bar"
    )

    adaptive_parser = subparsers.add_parser(
        "adaptive-run", help="Run an adaptive-attack experiment config (S4-05)"
    )
    adaptive_parser.add_argument("config", help="Path to a YAML experiment config")
    adaptive_parser.add_argument(
        "--concurrency", type=int, default=4, help="Max concurrent trials (default: 4)"
    )
    adaptive_parser.add_argument(
        "--no-cache", action="store_true", help="Bypass the response cache for this run"
    )
    adaptive_parser.add_argument(
        "--no-progress", action="store_true", help="Disable the progress bar"
    )
    adaptive_parser.add_argument(
        "--sample-fraction",
        type=float,
        default=None,
        help=(
            "Run a deterministic subsample of the trial grid instead of all of it "
            "(e.g. 0.01 for ~1%%) -- use to extrapolate cost/wall-clock before a full run"
        ),
    )

    args = parser.parse_args()

    if args.command == "run":
        config = load_config(args.config)
        run_specs = expand_run_specs(config)
        n_episodes = sum(len(spec.tasks) for spec in run_specs)
        print(f"{config.name}: {len(run_specs)} run spec(s), {n_episodes} episode(s)")

        summary = run_sweep(
            config,
            concurrency=args.concurrency,
            no_cache=args.no_cache,
            show_progress=not args.no_progress,
        )

        print(
            f"done: {summary.completed} completed, {summary.already_done} already done "
            f"(resumed), {summary.failed} failed, {summary.total_points} total"
        )
        for failure in summary.failures:
            print(
                f"  FAILED model={failure.model} defense={failure.defense} "
                f"suite={failure.suite} attack={failure.attack} task={failure.task}: "
                f"{failure.error}"
            )
        if summary.failed:
            raise SystemExit(1)

    elif args.command == "adaptive-run":
        config = load_config(args.config)
        trial_points = expand_adaptive_trial_points(config)
        n_trials = len(trial_points)
        if args.sample_fraction is not None:
            n_trials = max(1, round(n_trials * args.sample_fraction))
        max_episodes = n_trials * ADAPTIVE_ROUND_BUDGET
        print(
            f"{config.name}: {n_trials} trial(s), budget={ADAPTIVE_ROUND_BUDGET} rounds each "
            f"-- up to {max_episodes} episode(s) if nothing ever succeeds early"
        )

        adaptive_summary = run_adaptive_sweep(
            config,
            concurrency=args.concurrency,
            no_cache=args.no_cache,
            show_progress=not args.no_progress,
            sample_fraction=args.sample_fraction,
        )

        print(
            f"done: {adaptive_summary.completed} completed, "
            f"{adaptive_summary.already_done} already done (resumed), "
            f"{adaptive_summary.failed} failed, {adaptive_summary.total_trials} total"
        )
        for adaptive_failure in adaptive_summary.failures:
            print(
                f"  FAILED model={adaptive_failure.model} defense={adaptive_failure.defense} "
                f"suite={adaptive_failure.suite} attack={adaptive_failure.attack} "
                f"task={adaptive_failure.task}: {adaptive_failure.error}"
            )
        if adaptive_summary.failed:
            raise SystemExit(1)


if __name__ == "__main__":
    main()
