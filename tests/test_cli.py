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
