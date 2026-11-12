"""Tests for envy CLI interface."""

import io
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

from envy.cli import main
from tests.test_resolver import make_dummy_elf, make_dummy_pe


class TestCLI(unittest.TestCase):
    def test_cli_resolve_found(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td).resolve()
            venv_dir = root / ".venv"
            scripts_dir = venv_dir / ("Scripts" if sys.platform == "win32" else "bin")
            scripts_dir.mkdir(parents=True)
            (scripts_dir / ("Activate.ps1" if sys.platform == "win32" else "activate")).touch()

            if sys.platform == "win32":
                (scripts_dir / "python.exe").write_bytes(make_dummy_pe())
            else:
                (scripts_dir / "python").write_bytes(make_dummy_elf())

            with patch("sys.argv", ["envy", "resolve", str(root)]):
                captured = io.StringIO()
                with patch("sys.stdout", captured):
                    main()
                self.assertEqual(captured.getvalue().strip(), str(venv_dir))

    def test_cli_resolve_none(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td).resolve()
            with patch("sys.argv", ["envy", "resolve", str(root)]):
                captured = io.StringIO()
                with patch("sys.stdout", captured):
                    main()
                self.assertEqual(captured.getvalue().strip(), "")

    def test_cli_resolve_broken_environment_reports_diagnostic_to_stderr(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td).resolve()
            venv_dir = root / ".venv"
            scripts_dir = venv_dir / ("Scripts" if sys.platform == "win32" else "bin")
            scripts_dir.mkdir(parents=True)
            (scripts_dir / ("Activate.ps1" if sys.platform == "win32" else "activate")).touch()
            # 0-byte python interpreter is invalid
            py_name = "python.exe" if sys.platform == "win32" else "python"
            (scripts_dir / py_name).touch()

            with patch("sys.argv", ["envy", "resolve", str(root)]):
                captured_out = io.StringIO()
                captured_err = io.StringIO()
                with patch("sys.stdout", captured_out), patch("sys.stderr", captured_err):
                    with self.assertRaises(SystemExit) as cm:
                        main()
                    self.assertEqual(cm.exception.code, 1)

                # stdout must be empty to avoid activating broken venv
                self.assertEqual(captured_out.getvalue().strip(), "")
                # stderr must contain diagnostic message
                self.assertIn("envy: error:", captured_err.getvalue())
                self.assertIn("empty", captured_err.getvalue())

    def test_cli_resolve_nested_nearest(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td).resolve()
            parent_venv = root / ".venv"
            child = root / "child"
            child_venv = child / ".venv"

            for venv in (parent_venv, child_venv):
                sdir = venv / ("Scripts" if sys.platform == "win32" else "bin")
                sdir.mkdir(parents=True)
                (sdir / ("Activate.ps1" if sys.platform == "win32" else "activate")).touch()
                if sys.platform == "win32":
                    (sdir / "python.exe").write_bytes(make_dummy_pe())
                else:
                    (sdir / "python").write_bytes(make_dummy_elf())

            # Resolving from child resolves child_venv
            with patch("sys.argv", ["envy", "resolve", str(child)]):
                captured = io.StringIO()
                with patch("sys.stdout", captured):
                    main()
                self.assertEqual(captured.getvalue().strip(), str(child_venv))

    def test_cli_init_powershell(self):
        with patch("sys.argv", ["envy", "init", "powershell"]):
            captured = io.StringIO()
            with patch("sys.stdout", captured):
                main()
            output = captured.getvalue()
            self.assertIn("$global:__envy_hooked", output)
            self.assertIn("__envy_hook", output)
            self.assertIn("function global:prompt", output)
            self.assertIn("$global:__envy_prompt_old", output)
            self.assertIn("ENVY_MANAGED_VENV", output)
            self.assertIn("Activate.ps1", output)

    def test_cli_init_bash(self):
        with patch("sys.argv", ["envy", "init", "bash"]):
            captured = io.StringIO()
            with patch("sys.stdout", captured):
                main()
            output = captured.getvalue()
            self.assertIn("__ENVY_HOOKED", output)
            self.assertIn("__envy_hook", output)
            self.assertIn("PROMPT_COMMAND", output)
            self.assertIn("ENVY_MANAGED_VENV", output)
            self.assertIn("bin/activate", output)


if __name__ == "__main__":
    unittest.main()
