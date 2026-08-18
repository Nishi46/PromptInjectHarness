import argparse

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


if __name__ == "__main__":
    main()
