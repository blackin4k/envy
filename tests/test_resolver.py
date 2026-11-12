"""Tests for envy resolver and interpreter validation."""

import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

from envy.resolver import (
    EnvironmentStatus,
    ResolutionResult,
    ValidationResult,
    find_environment,
    get_activation_script,
    get_environment_names,
    is_native_linux_interpreter,
    is_native_windows_interpreter,
    is_valid_environment,
    resolve_environment,
    validate_environment,
)


def make_dummy_pe() -> bytes:
    """Create a minimal 88-byte valid Windows Portable Executable (PE) header."""
    dos_header = bytearray(b"MZ" + b"\x00" * 58)
    dos_header.extend(int.to_bytes(0x40, 4, "little"))  # e_lfanew = 0x40
    coff_header = b"PE\x00\x00" + int.to_bytes(0x8664, 2, "little") + b"\x00" * 18
    return bytes(dos_header + coff_header)


def make_dummy_elf() -> bytes:
    """Create a minimal 20-byte valid Linux ELF x86_64 executable header."""
    # Magic \x7fELF, class 2 (64-bit), data 1 (little-endian), version 1, abi 0, 8 bytes padding, type 2 (ET_EXEC), machine 0x3E (x86_64)
    return b"\x7fELF\x02\x01\x01\x00" + b"\x00" * 8 + b"\x02\x00\x3e\x00"


def setup_mock_venv(env_dir: Path, platform_type: str = "nt"):
    """Helper to populate a directory with a valid mock virtualenv for the platform."""
    if platform_type == "nt":
        scripts_dir = env_dir / "Scripts"
        scripts_dir.mkdir(parents=True, exist_ok=True)
        (scripts_dir / "Activate.ps1").touch()
        (scripts_dir / "activate").touch()
        (scripts_dir / "python.exe").write_bytes(make_dummy_pe())
    else:
        bin_dir = env_dir / "bin"
        bin_dir.mkdir(parents=True, exist_ok=True)
        (bin_dir / "activate").touch()
        (bin_dir / "python").write_bytes(make_dummy_elf())


class TestResolver(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name).resolve()

    def tearDown(self):
        self.temp_dir.cleanup()

    # --- Activation Script Resolution ---

    def test_get_activation_script_explicit_shells(self):
        fake_env = self.root / ".venv"
        ps_script = get_activation_script(fake_env, shell="powershell")
        bash_script = get_activation_script(fake_env, shell="bash")

        if os.name == "nt":
            self.assertEqual(ps_script, fake_env / "Scripts" / "Activate.ps1")
            self.assertEqual(bash_script, fake_env / "Scripts" / "activate")
        else:
            self.assertEqual(ps_script, fake_env / "bin" / "Activate.ps1")
            self.assertEqual(bash_script, fake_env / "bin" / "activate")

    def test_get_activation_script_auto_prefers_ps1_on_windows(self):
        fake_env = self.root / ".venv"
        scripts_dir = fake_env / ("Scripts" if os.name == "nt" else "bin")
        scripts_dir.mkdir(parents=True)

        ps1 = scripts_dir / "Activate.ps1"
        ps1.touch()

        script = get_activation_script(fake_env, shell="auto")
        if os.name == "nt":
            self.assertEqual(script, ps1)

    # --- Directory Crawling & Resolution ---

    def test_find_environment_in_current_dir(self):
        venv_dir = self.root / ".venv"
        setup_mock_venv(venv_dir, platform_type=os.name)

        found = find_environment(self.root)
        self.assertEqual(found, venv_dir)

    def test_find_environment_in_parent_dir(self):
        venv_dir = self.root / ".venv"
        setup_mock_venv(venv_dir, platform_type=os.name)

        sub_dir = self.root / "sub1" / "sub2"
        sub_dir.mkdir(parents=True)

        found = find_environment(sub_dir)
        self.assertEqual(found, venv_dir)

    def test_find_environment_when_start_is_file(self):
        venv_dir = self.root / ".venv"
        setup_mock_venv(venv_dir, platform_type=os.name)

        file_path = self.root / "main.py"
        file_path.touch()

        found = find_environment(file_path)
        self.assertEqual(found, venv_dir)

    def test_find_environment_none_found(self):
        empty_dir = self.root / "empty"
        empty_dir.mkdir()
        found = find_environment(empty_dir)
        self.assertIsNone(found)

    def test_find_environment_supports_names(self):
        for name in ("venv", "env"):
            with tempfile.TemporaryDirectory() as td:
                root = Path(td).resolve()
                env_dir = root / name
                setup_mock_venv(env_dir, platform_type=os.name)

                found = find_environment(root)
                self.assertEqual(found, env_dir)

    # --- Windows PE Interpreter Validation Regression Tests ---

    def test_is_native_windows_interpreter_valid(self):
        pe_file = self.root / "valid.exe"
        pe_file.write_bytes(make_dummy_pe())
        self.assertTrue(is_native_windows_interpreter(pe_file))

        if sys.platform == "win32":
            self.assertTrue(is_native_windows_interpreter(Path(sys.executable)))

    def test_is_native_windows_interpreter_rejects_empty_file(self):
        empty_file = self.root / "empty.exe"
        empty_file.touch()
        self.assertFalse(is_native_windows_interpreter(empty_file))

    def test_is_native_windows_interpreter_rejects_invalid_headers(self):
        # Truncated DOS header (< 64 bytes)
        truncated = self.root / "truncated.exe"
        truncated.write_bytes(b"MZ12345")
        self.assertFalse(is_native_windows_interpreter(truncated))

        # Plain text
        text_file = self.root / "script.exe"
        text_file.write_text("echo hello", encoding="utf-8")
        self.assertFalse(is_native_windows_interpreter(text_file))

        # Linux ELF binary
        elf_file = self.root / "elf.exe"
        elf_file.write_bytes(make_dummy_elf())
        self.assertFalse(is_native_windows_interpreter(elf_file))

        # DOS header with out-of-bounds e_lfanew
        bad_offset = bytearray(b"MZ" + b"\x00" * 58)
        bad_offset.extend(int.to_bytes(0x20, 4, "little"))  # < 64
        f1 = self.root / "bad_offset.exe"
        f1.write_bytes(bad_offset)
        self.assertFalse(is_native_windows_interpreter(f1))

        # DOS header pointing to non-PE signature
        bad_sig = bytearray(b"MZ" + b"\x00" * 58)
        bad_sig.extend(int.to_bytes(0x40, 4, "little"))
        bad_sig.extend(b"NOT_PE_SIG")
        f2 = self.root / "bad_sig.exe"
        f2.write_bytes(bad_sig)
        self.assertFalse(is_native_windows_interpreter(f2))

        # DOS header pointing to PE with unsupported machine architecture
        bad_arch = bytearray(b"MZ" + b"\x00" * 58)
        bad_arch.extend(int.to_bytes(0x40, 4, "little"))
        bad_arch.extend(b"PE\x00\x00" + int.to_bytes(0x9999, 2, "little") + b"\x00" * 18)
        f3 = self.root / "bad_arch.exe"
        f3.write_bytes(bad_arch)
        self.assertFalse(is_native_windows_interpreter(f3))

    # --- Linux ELF Interpreter Validation Regression Tests ---

    def test_is_native_linux_interpreter_valid(self):
        elf_file = self.root / "valid_elf"
        elf_file.write_bytes(make_dummy_elf())
        self.assertTrue(is_native_linux_interpreter(elf_file))

    def test_is_native_linux_interpreter_shebang_wrapper(self):
        target_elf = self.root / "real_python"
        target_elf.write_bytes(make_dummy_elf())

        wrapper = self.root / "py_wrapper"
        wrapper.write_text(f"#!{target_elf}\n", encoding="utf-8")
        self.assertTrue(is_native_linux_interpreter(wrapper))

    def test_is_native_linux_interpreter_rejects_empty_and_invalid(self):
        empty_file = self.root / "empty_elf"
        empty_file.touch()
        self.assertFalse(is_native_linux_interpreter(empty_file))

        pe_file = self.root / "fake_pe"
        pe_file.write_bytes(make_dummy_pe())
        self.assertFalse(is_native_linux_interpreter(pe_file))

        bad_elf = self.root / "bad_class_elf"
        bad_elf.write_bytes(b"\x7fELF\x09\x01\x01\x00" + b"\x00" * 8 + b"\x02\x00\x3e\x00")
        self.assertFalse(is_native_linux_interpreter(bad_elf))

        bad_shebang = self.root / "bad_shebang"
        bad_shebang.write_text("#!/nonexistent/interpreter\n", encoding="utf-8")
        self.assertFalse(is_native_linux_interpreter(bad_shebang))

    # --- Environment Validation Edge Cases (Requirements 1, 2, 4) ---

    @unittest.skipUnless(os.name == "nt", "Windows-specific test")
    def test_windows_missing_interpreter_rejected(self):
        venv = self.root / ".venv"
        scripts = venv / "Scripts"
        scripts.mkdir(parents=True)
        (scripts / "Activate.ps1").touch()
        # Scripts/python.exe does not exist
        self.assertFalse(is_valid_environment(venv, shell="powershell"))

    @unittest.skipUnless(os.name == "nt", "Windows-specific test")
    def test_windows_empty_interpreter_rejected(self):
        venv = self.root / ".venv"
        scripts = venv / "Scripts"
        scripts.mkdir(parents=True)
        (scripts / "Activate.ps1").touch()
        (scripts / "python.exe").touch()  # 0 bytes
        self.assertFalse(is_valid_environment(venv, shell="powershell"))

    def test_posix_missing_interpreter_rejected(self):
        with patch("envy.resolver.os.name", "posix"):
            venv = self.root / ".venv"
            bin_dir = venv / "bin"
            bin_dir.mkdir(parents=True)
            (bin_dir / "activate").touch()
            # bin/python does not exist
            self.assertFalse(is_valid_environment(venv, shell="bash"))

    def test_posix_invalid_interpreter_rejected(self):
        with patch("envy.resolver.os.name", "posix"):
            venv = self.root / ".venv"
            bin_dir = venv / "bin"
            bin_dir.mkdir(parents=True)
            (bin_dir / "activate").touch()
            # bin/python is Windows PE binary instead of ELF
            (bin_dir / "python").write_bytes(make_dummy_pe())
            self.assertFalse(is_valid_environment(venv, shell="bash"))

    # --- Cross-Platform Rejection Tests (Requirement 6) ---

    def test_windows_rejects_linux_environment(self):
        linux_venv = self.root / "linux_project" / ".venv"
        setup_mock_venv(linux_venv, platform_type="posix")

        with patch("envy.resolver.os.name", "nt"):
            self.assertFalse(is_valid_environment(linux_venv, shell="powershell"))
            self.assertIsNone(find_environment(linux_venv.parent, shell="powershell"))

    def test_posix_rejects_windows_environment(self):
        win_venv = self.root / "win_project" / ".venv"
        setup_mock_venv(win_venv, platform_type="nt")

        with patch("envy.resolver.os.name", "posix"):
            self.assertFalse(is_valid_environment(win_venv, shell="bash"))
            self.assertIsNone(find_environment(win_venv.parent, shell="bash"))

    # --- Nested Project Environments (Requirement 1) ---

    def test_nested_project_environments(self):
        """Verify nearest-environment resolution in nested project hierarchies.

        Structure:
          workspace/
            .venv/
            project-a/
              .venv/
              backend/
                .venv/
              frontend/
        """
        workspace = self.root / "workspace"
        project_a = workspace / "project-a"
        backend = project_a / "backend"
        frontend = project_a / "frontend"

        frontend.mkdir(parents=True)
        setup_mock_venv(workspace / ".venv", platform_type=os.name)
        setup_mock_venv(project_a / ".venv", platform_type=os.name)
        setup_mock_venv(backend / ".venv", platform_type=os.name)

        # 1. backend/ uses backend/.venv
        res_backend = resolve_environment(backend)
        self.assertTrue(res_backend.is_valid)
        self.assertEqual(res_backend.environment, backend / ".venv")

        # 2. frontend/ has no venv, walking up finds project-a/.venv
        res_frontend = resolve_environment(frontend)
        self.assertTrue(res_frontend.is_valid)
        self.assertEqual(res_frontend.environment, project_a / ".venv")

        # 3. project-a/ uses project-a/.venv
        res_project_a = resolve_environment(project_a)
        self.assertTrue(res_project_a.is_valid)
        self.assertEqual(res_project_a.environment, project_a / ".venv")

        # 4. workspace/ uses workspace/.venv
        res_workspace = resolve_environment(workspace)
        self.assertTrue(res_workspace.is_valid)
        self.assertEqual(res_workspace.environment, workspace / ".venv")

        # 5. Outside workspace results in MISSING (no environment)
        outside = self.root / "outside"
        outside.mkdir()
        res_outside = resolve_environment(outside)
        self.assertEqual(res_outside.status, EnvironmentStatus.MISSING)
        self.assertIsNone(res_outside.environment)

    # --- Precedence & Deterministic Priority Ordering (Requirement 2) ---

    def test_environment_precedence_ordering(self):
        """Verify candidate names are resolved in deterministic descending priority."""
        proj = self.root / "precedence_proj"
        proj.mkdir()

        names = get_environment_names()
        # Create all candidates with valid venvs
        for name in names:
            setup_mock_venv(proj / name, platform_type=os.name)

        # Iteratively verify the highest priority candidate wins and then remove it
        for expected_name in names:
            res = resolve_environment(proj)
            self.assertTrue(res.is_valid)
            self.assertEqual(res.environment, proj / expected_name)
            # Remove highest priority candidate to verify next one in order
            shutil_rm = proj / expected_name
            import shutil
            shutil.rmtree(shutil_rm)

    # --- Broken Environment Handling & No-Fallback Product Decision (Requirement 3) ---

    def test_broken_candidate_in_same_dir_prevents_fallback(self):
        """Product decision: An invalid highest-priority candidate in a directory
        must NOT fall back to a valid lower-priority candidate in the same directory.
        """
        proj = self.root / "broken_same_dir"
        proj.mkdir()

        names = get_environment_names()
        highest_name = names[0]  # e.g. .venv-windows on Windows, .venv-linux on Linux
        lower_name = names[-1]   # e.g. env

        # Create highest-priority candidate, but make it corrupted (empty interpreter)
        broken_candidate = proj / highest_name
        scripts_dir = broken_candidate / ("Scripts" if os.name == "nt" else "bin")
        scripts_dir.mkdir(parents=True)
        (scripts_dir / ("Activate.ps1" if os.name == "nt" else "activate")).touch()
        py_name = "python.exe" if os.name == "nt" else "python"
        (scripts_dir / py_name).touch()  # 0 bytes = INVALID

        # Create lower-priority candidate that is completely VALID
        valid_lower_candidate = proj / lower_name
        setup_mock_venv(valid_lower_candidate, platform_type=os.name)

        # Resolution must NOT silently fall back to valid_lower_candidate
        res = resolve_environment(proj)
        self.assertFalse(res.is_valid)
        self.assertEqual(res.status, EnvironmentStatus.INVALID)
        self.assertEqual(res.candidate, broken_candidate)
        self.assertIsNone(res.environment)
        self.assertIn("empty", res.error_message)

        # find_environment wrapper must also return None
        self.assertIsNone(find_environment(proj))

    def test_broken_candidate_in_nearest_project_prevents_ancestor_fallback(self):
        """Product decision: An invalid candidate in a child project must NOT
        silently fall back to an ancestor project's valid environment.
        """
        workspace = self.root / "workspace_ancestor"
        workspace.mkdir()
        setup_mock_venv(workspace / ".venv", platform_type=os.name)

        # Child project has an INCOMPLETE .venv (missing interpreter)
        child = workspace / "child_project"
        child_venv = child / ".venv"
        scripts_dir = child_venv / ("Scripts" if os.name == "nt" else "bin")
        scripts_dir.mkdir(parents=True)
        (scripts_dir / ("Activate.ps1" if os.name == "nt" else "activate")).touch()
        # Interpreter is missing -> INCOMPLETE

        # Resolution from child must NOT silently activate workspace/.venv
        res = resolve_environment(child)
        self.assertFalse(res.is_valid)
        self.assertEqual(res.status, EnvironmentStatus.INCOMPLETE)
        self.assertEqual(res.candidate, child_venv)
        self.assertEqual(res.nearest_project_dir, child)
        self.assertIsNone(res.environment)
        self.assertIn("missing", res.error_message.lower())

        # find_environment wrapper must also return None
        self.assertIsNone(find_environment(child))

    def test_status_categorization_and_diagnostics(self):
        """Verify detailed status categorization: VALID, MISSING, INCOMPLETE, INVALID."""
        proj = self.root / "diag_proj"
        proj.mkdir()

        # 1. MISSING
        missing_res = validate_environment(proj / "nonexistent")
        self.assertEqual(missing_res.status, EnvironmentStatus.MISSING)

        # 2. INVALID (path is a regular file)
        file_candidate = proj / "file_as_env"
        file_candidate.touch()
        file_res = validate_environment(file_candidate)
        self.assertEqual(file_res.status, EnvironmentStatus.INVALID)
        self.assertIn("file, not a directory", file_res.message)

        # 3. INCOMPLETE (missing Scripts/bin directory)
        empty_dir_candidate = proj / "empty_dir_env"
        empty_dir_candidate.mkdir()
        empty_res = validate_environment(empty_dir_candidate)
        self.assertEqual(empty_res.status, EnvironmentStatus.INCOMPLETE)

        # 4. INCOMPLETE (missing activation script)
        no_act = proj / "no_act_env"
        sub = no_act / ("Scripts" if os.name == "nt" else "bin")
        sub.mkdir(parents=True)
        no_act_res = validate_environment(no_act)
        self.assertEqual(no_act_res.status, EnvironmentStatus.INCOMPLETE)
        self.assertIn("activation script", no_act_res.message)

        # 5. VALID
        valid_env = proj / "valid_env"
        setup_mock_venv(valid_env, platform_type=os.name)
        valid_res = validate_environment(valid_env)
        self.assertEqual(valid_res.status, EnvironmentStatus.VALID)

    # --- Symlink and Path Handling Edge Cases (Requirement 5) ---

    def test_symlinked_virtual_environment(self):
        """Verify resolution when the environment directory itself is a symlink."""
        target_venv = self.root / "shared_venvs" / "my_target_venv"
        setup_mock_venv(target_venv, platform_type=os.name)

        proj = self.root / "symlink_proj"
        proj.mkdir()
        link_venv = proj / ".venv"

        try:
            link_venv.symlink_to(target_venv, target_is_directory=True)
        except (OSError, NotImplementedError):
            self.skipTest("Symlinks not permitted or supported in this test environment")

        res = resolve_environment(proj)
        self.assertTrue(res.is_valid)
        self.assertEqual(res.status, EnvironmentStatus.VALID)
        self.assertEqual(res.environment, link_venv)

    def test_broken_symlink_environment_candidate(self):
        """Verify broken symlink environment candidate is detected as INVALID."""
        proj = self.root / "broken_symlink_proj"
        proj.mkdir()
        broken_link = proj / ".venv"

        try:
            broken_link.symlink_to(self.root / "nonexistent_target", target_is_directory=True)
        except (OSError, NotImplementedError):
            self.skipTest("Symlinks not permitted or supported in this test environment")

        res = resolve_environment(proj)
        self.assertFalse(res.is_valid)
        self.assertEqual(res.status, EnvironmentStatus.INVALID)
        self.assertIn("broken symlink", res.error_message)

    def test_paths_with_spaces_and_unicode(self):
        """Verify resolution succeeds with spaces and Unicode characters in project paths."""
        unicode_proj = self.root / "pröjéct space 🚀"
        unicode_proj.mkdir()
        setup_mock_venv(unicode_proj / ".venv", platform_type=os.name)

        # Nested directory with spaces
        nested = unicode_proj / "sub directory with spaces"
        nested.mkdir()

        res = resolve_environment(nested)
        self.assertTrue(res.is_valid)
        self.assertEqual(res.environment, unicode_proj / ".venv")


if __name__ == "__main__":
    unittest.main()
