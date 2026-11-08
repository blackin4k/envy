import os
from pathlib import Path

ENV_NAMES = (".venv", "venv", "env")


def get_activation_script(environment: Path) -> Path:
    if os.name == "nt":
        return environment / "Scripts" / "activate"
    return environment / "bin" / "activate"


def find_environment(start: Path | None = None) -> Path | None:
    current = (start or Path.cwd()).resolve()

    if current.is_file():
        current = current.parent

    for directory in (current, *current.parents):
        for name in ENV_NAMES:
            environment = directory / name
            activation_script = get_activation_script(environment)

            if activation_script.is_file():
                return environment

    return None
