import argparse
from pathlib import Path

from envy.resolver import find_environment
from envy.shells.bash import get_bash_hook
from envy.shells.powershell import get_powershell_hook


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

    resolve_parser.add_argument(
        "--shell",
        choices=["powershell", "bash", "auto"],
        default="auto",
        help="Target shell for environment resolution",
    )

    init_parser = subparsers.add_parser(
        "init",
        help="Generate shell hook script for automatic environment switching",
    )

    init_parser.add_argument(
        "shell",
        choices=["powershell", "bash"],
        help="Shell to initialize",
    )

    args = parser.parse_args()

    if args.command == "resolve":
        environment = find_environment(Path(args.path), shell=args.shell)

        if environment is None:
            return

        print(environment)

    elif args.command == "init":
        if args.shell == "powershell":
            print(get_powershell_hook())
        elif args.shell == "bash":
            print(get_bash_hook())


if __name__ == "__main__":
    main()
