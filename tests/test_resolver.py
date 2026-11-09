"""Tests for envy resolver and interpreter validation."""

import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

from envy.resolver import (
    find_environment,
    get_activation_script,
    is_native_linux_interpreter,
    is_native_windows_interpreter,
    is_valid_environment,
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


if __name__ == "__main__":
    unittest.main()
