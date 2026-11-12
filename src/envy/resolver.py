"""Envy virtual environment resolver and validator.

Discovers, validates, and resolves Python virtual environments
with deterministic precedence, strict platform isolation, and
nearest-project resolution semantics.
"""

from enum import Enum, auto
import os
from pathlib import Path
import sys

# Deterministic priority order for environment candidate names (highest priority first)
WINDOWS_ENV_NAMES = (".venv-windows", ".venv-win", ".venv", "venv", "env")
POSIX_ENV_NAMES = (".venv-linux", ".venv-wsl", ".venv", "venv", "env")

# Recognized Windows PE machine architectures
VALID_PE_MACHINES = {
    0x8664: "x86_64 / AMD64",
    0x014C: "i386 / x86",
    0xAA64: "ARM64",
    0x01C0: "ARM (little-endian)",
    0x01C4: "ARMv7 (Thumb-2)",
}


class EnvironmentStatus(Enum):
    """Categorized status of an environment candidate."""
    VALID = auto()        # Directory is a complete and valid virtualenv native to current platform
    MISSING = auto()      # No environment candidate directory was found
    INCOMPLETE = auto()   # Candidate directory exists but is missing required files or subdirectories
    INVALID = auto()      # Candidate directory exists but contains malformed or cross-platform files


class ValidationResult:
    """Outcome of validating a candidate virtual environment directory."""

    def __init__(self, status: EnvironmentStatus, path: Path, message: str = ""):
        self.status = status
        self.path = path
        self.message = message

    @property
    def is_valid(self) -> bool:
        return self.status == EnvironmentStatus.VALID

    def __repr__(self) -> str:
        return f"ValidationResult(status={self.status.name}, path={self.path}, message={self.message!r})"


class ResolutionResult:
    """Outcome of resolving an environment for a starting directory path."""

    def __init__(
        self,
        environment: Path | None = None,
        status: EnvironmentStatus = EnvironmentStatus.MISSING,
        candidate: Path | None = None,
        error_message: str | None = None,
        nearest_project_dir: Path | None = None,
    ):
        self.environment = environment
        self.status = status
        self.candidate = candidate
        self.error_message = error_message
        self.nearest_project_dir = nearest_project_dir

    @property
    def is_valid(self) -> bool:
        return self.status == EnvironmentStatus.VALID and self.environment is not None

    def __repr__(self) -> str:
        return (
            f"ResolutionResult(environment={self.environment}, status={self.status.name}, "
            f"candidate={self.candidate}, error_message={self.error_message!r})"
        )


def validate_windows_interpreter(python_path: Path) -> tuple[bool, str]:
    """Validate that python_path resolves to a native Windows PE executable.

    Returns (is_valid, diagnostic_message).
    """
    try:
        if not python_path.exists() and not python_path.is_symlink():
            return False, f"Interpreter '{python_path}' does not exist"

        resolved = python_path.resolve()
        if not resolved.is_file():
            return False, f"Interpreter '{python_path}' does not resolve to a regular file"

        with open(resolved, "rb") as f:
            dos_header = f.read(64)

        if len(dos_header) == 0:
            return False, f"Interpreter '{python_path}' is empty (0 bytes)"

        if len(dos_header) < 64:
            return False, f"Interpreter '{python_path}' DOS header is truncated ({len(dos_header)} bytes < 64)"

        if dos_header[:2] != b"MZ":
            if dos_header[:4] == b"\x7fELF":
                return False, f"Interpreter '{python_path}' is a Linux ELF binary, not a Windows executable"
            return False, f"Interpreter '{python_path}' lacks DOS 'MZ' header signature"

        e_lfanew = int.from_bytes(dos_header[0x3C:0x40], "little")
        if e_lfanew < 64 or e_lfanew > 10 * 1024 * 1024:
            return False, f"Interpreter '{python_path}' has out-of-bounds PE offset ({e_lfanew})"

        with open(resolved, "rb") as f:
            f.seek(e_lfanew)
            pe_signature = f.read(4)
            if pe_signature != b"PE\x00\x00":
                return False, f"Interpreter '{python_path}' lacks valid 'PE\\0\\0' signature at offset {e_lfanew}"

            machine_bytes = f.read(2)
            if len(machine_bytes) < 2:
                return False, f"Interpreter '{python_path}' COFF header is truncated"

            machine = int.from_bytes(machine_bytes, "little")
            if machine not in VALID_PE_MACHINES:
                return False, f"Interpreter '{python_path}' has unsupported PE machine architecture ({hex(machine)})"

        return True, "Valid Windows PE executable"
    except (OSError, PermissionError) as exc:
        return False, f"Cannot read interpreter '{python_path}': {exc}"


def is_native_windows_interpreter(python_path: Path) -> bool:
    """Validate that python_path resolves to a native Windows PE executable."""
    return validate_windows_interpreter(python_path)[0]


def validate_linux_interpreter(python_path: Path, max_depth: int = 5) -> tuple[bool, str]:
    """Validate that python_path resolves to a native Linux ELF executable.

    Handles shebang script wrappers up to max_depth.
    Returns (is_valid, diagnostic_message).
    """
    if max_depth <= 0:
        return False, f"Exceeded maximum recursion depth resolving shebang wrapper '{python_path}'"

    try:
        if not python_path.exists() and not python_path.is_symlink():
            return False, f"Interpreter '{python_path}' does not exist"

        resolved = python_path.resolve()
        if not resolved.is_file():
            return False, f"Interpreter '{python_path}' does not resolve to a regular file"

        with open(resolved, "rb") as f:
            header = f.read(20)

        if len(header) == 0:
            return False, f"Interpreter '{python_path}' is empty (0 bytes)"

        if len(header) < 18:
            return False, f"Interpreter '{python_path}' header is truncated ({len(header)} bytes < 18)"

        # Check for ELF magic bytes: \x7fELF
        if header[:4] == b"\x7fELF":
            elf_class = header[4]  # 1: 32-bit, 2: 64-bit
            elf_data = header[5]   # 1: little-endian, 2: big-endian
            if elf_class not in (1, 2):
                return False, f"Interpreter '{python_path}' has invalid ELF class ({elf_class})"
            if elf_data not in (1, 2):
                return False, f"Interpreter '{python_path}' has invalid ELF data encoding ({elf_data})"

            endian = "little" if elf_data == 1 else "big"
            elf_type = int.from_bytes(header[16:18], endian)
            # 2: ET_EXEC (standard executable), 3: ET_DYN (position-independent executable / shared object)
            if elf_type not in (2, 3):
                return False, f"Interpreter '{python_path}' has non-executable ELF type ({elf_type})"

            return True, "Valid Linux ELF executable"

        # Check if it is a Windows PE executable erroneously placed in Linux venv
        if header[:2] == b"MZ":
            return False, f"Interpreter '{python_path}' is a Windows PE binary, rejected on Linux"

        # Handle shebang script wrapper (e.g. #!/usr/bin/python3)
        if header.startswith(b"#!"):
            with open(resolved, "r", errors="ignore") as f:
                first_line = f.readline().strip()
            parts = first_line[2:].strip().split()
            if parts:
                shebang_target = Path(parts[0])
                if shebang_target.is_file():
                    return validate_linux_interpreter(shebang_target, max_depth=max_depth - 1)
                return False, f"Shebang interpreter target '{shebang_target}' does not exist"

        return False, f"Interpreter '{python_path}' is not a Linux ELF binary or supported script wrapper"
    except (OSError, PermissionError) as exc:
        return False, f"Cannot read interpreter '{python_path}': {exc}"


def is_native_linux_interpreter(python_path: Path) -> bool:
    """Validate that python_path resolves to a native Linux ELF executable."""
    return validate_linux_interpreter(python_path)[0]


def get_activation_script(environment: Path, shell: str = "auto") -> Path | None:
    """Determine the expected activation script path for the environment and shell."""
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


def validate_environment(environment: Path, shell: str = "auto") -> ValidationResult:
    """Validate an environment candidate directory and return categorized status and diagnostics.

    Distinguishes:
    - VALID: Fully functional virtualenv native to current platform.
    - MISSING: Path does not exist.
    - INCOMPLETE: Directory exists but is missing required structure or files.
    - INVALID: Directory exists but is corrupt, malformed, or cross-platform incompatible.
    """
    if environment.is_symlink() and not environment.exists():
        return ValidationResult(
            EnvironmentStatus.INVALID,
            environment,
            f"Environment candidate '{environment}' is a broken symlink",
        )

    if not environment.exists() and not environment.is_symlink():
        return ValidationResult(
            EnvironmentStatus.MISSING,
            environment,
            f"Environment directory '{environment}' does not exist",
        )

    if not environment.is_dir():
        return ValidationResult(
            EnvironmentStatus.INVALID,
            environment,
            f"Environment path '{environment}' is a file, not a directory",
        )

    if os.name == "nt":
        scripts_dir = environment / "Scripts"
        # Check if environment is cross-platform Linux environment
        if not scripts_dir.is_dir() and (environment / "bin" / "python").exists():
            return ValidationResult(
                EnvironmentStatus.INVALID,
                environment,
                f"Environment '{environment}' is a Linux virtual environment (contains bin/), rejected on Windows",
            )

        if not scripts_dir.is_dir():
            return ValidationResult(
                EnvironmentStatus.INCOMPLETE,
                environment,
                f"Environment '{environment}' is incomplete: missing 'Scripts' directory",
            )

        act_script = get_activation_script(environment, shell=shell)
        if act_script is None or not act_script.is_file():
            script_name = act_script.name if act_script else "activation script"
            return ValidationResult(
                EnvironmentStatus.INCOMPLETE,
                environment,
                f"Environment '{environment}' is incomplete: activation script '{script_name}' is missing",
            )

        python_exe = scripts_dir / "python.exe"
        if not python_exe.exists() and not python_exe.is_symlink():
            return ValidationResult(
                EnvironmentStatus.INCOMPLETE,
                environment,
                f"Environment '{environment}' is incomplete: interpreter '{python_exe}' is missing",
            )

        is_valid_exe, msg = validate_windows_interpreter(python_exe)
        if not is_valid_exe:
            return ValidationResult(
                EnvironmentStatus.INVALID,
                environment,
                f"Environment '{environment}' is invalid: {msg}",
            )

        return ValidationResult(EnvironmentStatus.VALID, environment, "Valid Windows virtual environment")

    # POSIX / Linux / WSL
    bin_dir = environment / "bin"
    # Never accept a Windows-only environment in POSIX
    if (environment / "Scripts" / "python.exe").exists() and not (bin_dir / "python").exists():
        return ValidationResult(
            EnvironmentStatus.INVALID,
            environment,
            f"Environment '{environment}' is a Windows virtual environment (contains Scripts/), rejected on Linux",
        )

    if not bin_dir.is_dir():
        return ValidationResult(
            EnvironmentStatus.INCOMPLETE,
            environment,
            f"Environment '{environment}' is incomplete: missing 'bin' directory",
        )

    act_script = get_activation_script(environment, shell=shell)
    if act_script is None or not act_script.is_file():
        script_name = act_script.name if act_script else "activation script"
        return ValidationResult(
            EnvironmentStatus.INCOMPLETE,
            environment,
            f"Environment '{environment}' is incomplete: activation script '{script_name}' is missing",
        )

    candidates = ("python", "python3", "python3.13", "python3.12", "python3.11", "python3.10")
    existing_interpreters = [bin_dir / name for name in candidates if (bin_dir / name).exists() or (bin_dir / name).is_symlink()]

    if not existing_interpreters:
        return ValidationResult(
            EnvironmentStatus.INCOMPLETE,
            environment,
            f"Environment '{environment}' is incomplete: missing Python interpreter in '{bin_dir}'",
        )

    # Validate existing interpreter binaries
    last_err = ""
    for py_bin in existing_interpreters:
        is_valid_bin, err = validate_linux_interpreter(py_bin)
        if is_valid_bin:
            return ValidationResult(EnvironmentStatus.VALID, environment, "Valid Linux virtual environment")
        last_err = err

    return ValidationResult(
        EnvironmentStatus.INVALID,
        environment,
        f"Environment '{environment}' is invalid: {last_err}",
    )


def is_valid_environment(environment: Path, shell: str = "auto") -> bool:
    """Check if environment directory is a valid virtualenv native to current platform."""
    return validate_environment(environment, shell=shell).is_valid


def get_environment_names(shell: str = "auto") -> tuple[str, ...]:
    """Return the ordered candidate virtual environment names for the active platform."""
    if os.name == "nt":
        return WINDOWS_ENV_NAMES
    return POSIX_ENV_NAMES


def resolve_environment(start: Path | None = None, shell: str = "auto") -> ResolutionResult:
    """Resolve the nearest virtual environment starting from `start` and walking upward.

    Resolution Policy:
    1. Walks upward from the starting directory to ancestor directories.
    2. At each directory level, inspects environment candidates according to the
       platform's deterministic priority order.
    3. If one or more candidate directories exist in the current directory:
       - The highest-priority candidate is evaluated.
       - If valid, resolution succeeds immediately.
       - If invalid or incomplete:
         Per product decision, resolution STOPS immediately. Envy does NOT
         silently fall back to a lower-priority candidate in the same directory,
         nor does it silently activate an ancestor's environment. An error diagnostic
         is reported to prevent running with wrong or corrupted dependencies.
    4. If no candidate directories exist at the current level, continues searching
       ancestor directories (nearest-environment resolution).
    5. Returns ResolutionResult(status=MISSING) if no environment candidate exists.
    """
    if start is None:
        try:
            current = Path.cwd()
        except Exception:
            current = Path()
    elif isinstance(start, Path):
        current = start
    else:
        current = Path(start)

    if not current.is_absolute():
        try:
            current = Path(os.path.abspath(str(current)))
        except Exception:
            pass

    if current.is_file():
        current = current.parent

    env_names = get_environment_names(shell=shell)

    for directory in (current, *current.parents):
        # Discover existing candidates in deterministic priority order
        existing_candidates: list[Path] = []
        for name in env_names:
            candidate = directory / name
            if candidate.exists() or candidate.is_symlink():
                existing_candidates.append(candidate)

        if existing_candidates:
            # Nearest project directory found with at least one candidate.
            # Select the highest-priority candidate:
            highest_candidate = existing_candidates[0]
            val_result = validate_environment(highest_candidate, shell=shell)

            if val_result.is_valid:
                return ResolutionResult(
                    environment=highest_candidate,
                    status=EnvironmentStatus.VALID,
                    candidate=highest_candidate,
                    nearest_project_dir=directory,
                )

            # Highest-priority candidate exists but is incomplete or invalid.
            # Do NOT fall back to lower-priority candidate or parent environment.
            return ResolutionResult(
                environment=None,
                status=val_result.status,
                candidate=highest_candidate,
                error_message=val_result.message,
                nearest_project_dir=directory,
            )

    return ResolutionResult(
        environment=None,
        status=EnvironmentStatus.MISSING,
        error_message="No virtual environment found",
    )


def find_environment(start: Path | None = None, shell: str = "auto") -> Path | None:
    """Find the nearest valid environment or return None."""
    result = resolve_environment(start=start, shell=shell)
    return result.environment
