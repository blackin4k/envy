import os
from pathlib import Path

WINDOWS_ENV_NAMES = (".venv-windows", ".venv-win", ".venv", "venv", "env")
POSIX_ENV_NAMES = (".venv-linux", ".venv-wsl", ".venv", "venv", "env")

VALID_PE_MACHINES = {0x8664, 0x014C, 0xAA64, 0x01C0, 0x01C4}


def is_native_linux_interpreter(python_path: Path) -> bool:
    """Validate that python_path resolves to a native Linux ELF executable."""
    try:
        resolved = python_path.resolve()
        if not resolved.is_file():
            return False

        with open(resolved, "rb") as f:
            header = f.read(20)

        if len(header) < 18:
            return False

        # Check for ELF magic bytes: \x7fELF
        if header[:4] == b"\x7fELF":
            elf_class = header[4]  # 1: 32-bit, 2: 64-bit
            elf_data = header[5]   # 1: little-endian, 2: big-endian
            if elf_class not in (1, 2) or elf_data not in (1, 2):
                return False

            endian = "little" if elf_data == 1 else "big"
            elf_type = int.from_bytes(header[16:18], endian)
            # 2: ET_EXEC (standard executable), 3: ET_DYN (position-independent executable)
            return elf_type in (2, 3)

        # Handle shebang script wrapper (e.g. #!/usr/bin/python3)
        if header.startswith(b"#!"):
            with open(resolved, "r", errors="ignore") as f:
                first_line = f.readline().strip()
            parts = first_line[2:].strip().split()
            if parts:
                shebang_target = Path(parts[0])
                if shebang_target.is_file():
                    return is_native_linux_interpreter(shebang_target)

        return False
    except (OSError, PermissionError):
        return False


def is_native_windows_interpreter(python_path: Path) -> bool:
    """Validate that python_path resolves to a native Windows PE executable."""
    try:
        resolved = python_path.resolve()
        if not resolved.is_file():
            return False

        with open(resolved, "rb") as f:
            # Valid PE requires at least a 64-byte DOS header
            dos_header = f.read(64)
            if len(dos_header) < 64:
                return False

            # DOS signature must be 'MZ'
            if dos_header[:2] != b"MZ":
                return False

            # Offset to PE header is stored at 0x3C (4 bytes, little-endian)
            e_lfanew = int.from_bytes(dos_header[0x3C:0x40], "little")
            if e_lfanew < 64 or e_lfanew > 10 * 1024 * 1024:
                return False

            f.seek(e_lfanew)
            pe_signature = f.read(4)
            if pe_signature != b"PE\x00\x00":
                return False

            # Read COFF Machine architecture (2 bytes, little-endian)
            machine = int.from_bytes(f.read(2), "little")
            return machine in VALID_PE_MACHINES
    except (OSError, PermissionError):
        return False


def get_activation_script(environment: Path, shell: str = "auto") -> Path | None:
    if os.name == "nt":
        scripts_dir = environment / "Scripts"
        if shell == "powershell":
            return scripts_dir / "Activate.ps1"
        if shell == "bash":
            return scripts_dir / "activate"
        # auto: check for Activate.ps1 first, then activate
        ps_script = scripts_dir / "Activate.ps1"
        if ps_script.is_file():
            return ps_script
        return scripts_dir / "activate"

    # POSIX / Linux / WSL
    bin_dir = environment / "bin"
    if shell == "powershell":
        return bin_dir / "Activate.ps1"
    return bin_dir / "activate"


def is_valid_environment(environment: Path, shell: str = "auto") -> bool:
    """Check if environment directory is a valid virtualenv native to current platform."""
    if not environment.is_dir():
        return False

    act_script = get_activation_script(environment, shell=shell)
    if act_script is None or not act_script.is_file():
        return False

    if os.name == "nt":
        scripts_dir = environment / "Scripts"
        python_exe = scripts_dir / "python.exe"
        if not python_exe.is_file():
            return False
        return is_native_windows_interpreter(python_exe)

    # POSIX / Linux / WSL
    bin_dir = environment / "bin"
    # Never accept a Windows-only environment in POSIX
    if (environment / "Scripts" / "python.exe").is_file() and not (bin_dir / "python").is_file():
        return False

    for candidate in ("python", "python3", "python3.13", "python3.12", "python3.11", "python3.10"):
        py_bin = bin_dir / candidate
        if py_bin.is_file() or py_bin.is_symlink():
            if is_native_linux_interpreter(py_bin):
                return True

    return False


def get_environment_names(shell: str = "auto") -> tuple[str, ...]:
    if os.name == "nt":
        return WINDOWS_ENV_NAMES
    return POSIX_ENV_NAMES


def find_environment(start: Path | None = None, shell: str = "auto") -> Path | None:
    current = (start or Path.cwd()).resolve()

    if current.is_file():
        current = current.parent

    env_names = get_environment_names(shell=shell)

    for directory in (current, *current.parents):
        for name in env_names:
            environment = directory / name
            if is_valid_environment(environment, shell=shell):
                return environment

    return None
