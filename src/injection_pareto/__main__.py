import argparse


def main() -> None:
    parser = argparse.ArgumentParser(prog="injection_pareto")
    subparsers = parser.add_subparsers(dest="command", required=True)

    run_parser = subparsers.add_parser("run", help="Run an experiment config")
    run_parser.add_argument("config", help="Path to a YAML experiment config")

    args = parser.parse_args()

    if args.command == "run":
        raise NotImplementedError("Config system lands in S1-06; runner lands in S1-07.")


if __name__ == "__main__":
    main()
