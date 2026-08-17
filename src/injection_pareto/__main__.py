import argparse

from injection_pareto.config import expand_run_specs, load_config


def main() -> None:
    parser = argparse.ArgumentParser(prog="injection_pareto")
    subparsers = parser.add_subparsers(dest="command", required=True)

    run_parser = subparsers.add_parser("run", help="Run an experiment config")
    run_parser.add_argument("config", help="Path to a YAML experiment config")

    args = parser.parse_args()

    if args.command == "run":
        config = load_config(args.config)
        run_specs = expand_run_specs(config)
        print(f"{config.name}: {len(run_specs)} run(s)")
        for spec in run_specs:
            print(
                f"  - model={spec.model.id} defense={spec.defense} "
                f"suite={spec.suite} attack={spec.attack}"
            )
        raise NotImplementedError(
            "Config loads and expands correctly, but the AgentDojo adapter that "
            "actually executes a run spec lands in S1-07."
        )


if __name__ == "__main__":
    main()
