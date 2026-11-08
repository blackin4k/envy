import argparse
from pathlib import Path

from envy.resolver import find_environment


def main():
    parser = argparse.ArgumentParser(
        prog="envy",
        description="Automatic Python environment management",
    )

    subparsers = parser.add_subparsers(dest="command", required=True)

    resolve_parser = subparsers.add_parser(
        "resolve",
        help="Find the nearest Python virtual environment",
    )

    resolve_parser.add_argument(
        "path",
        nargs="?",
        default=".",
        help="Directory to inspect",
    )

    args = parser.parse_args()

    if args.command == "resolve":
        environment = find_environment(Path(args.path))

        if environment is None:
            return

        print(environment)


if __name__ == "__main__":
    main()
