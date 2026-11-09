"""Integration and edge-case tests for PowerShell hook."""

from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

from envy.shells.powershell import get_powershell_hook
from tests.test_resolver import make_dummy_pe


class TestPowerShellHook(unittest.TestCase):
    def test_hook_generation_contains_key_contracts(self):
        hook = get_powershell_hook(envy_bin="C:\\custom\\envy.exe")
        self.assertIn("C:\\custom\\envy.exe", hook)
        self.assertIn("$global:__envy_prompt_old = $function:prompt", hook)
        self.assertIn("& $global:__envy_prompt_old", hook)
        self.assertIn("function global:__envy_hook", hook)
        self.assertIn("function global:prompt", hook)
        self.assertIn("$global:ENVY_MANAGED_VENV", hook)
        self.assertIn("$global:ENVY_DEACTIVATED_DIR", hook)
        self.assertIn("$global:ENVY_FAILED_DIR", hook)
        self.assertIn("$global:LASTEXITCODE = $origLastExitCode", hook)
        self.assertIn("Activate.ps1", hook)
        self.assertIn('"envy: venv loaded"', hook)
        self.assertIn('"envy: venv unloaded"', hook)

    @unittest.skipUnless(sys.platform == "win32", "PowerShell tests require Windows")
    def test_powershell_notifications(self):
        """Test minimal activation/deactivation notifications in PowerShell."""
        pwsh = shutil.which("powershell.exe") or shutil.which("powershell")
        if not pwsh:
            self.skipTest("powershell executable not found")

        with tempfile.TemporaryDirectory() as td:
            base = Path(td).resolve()
            proj_a = base / "projA"
            proj_b = base / "projB"
            no_venv = base / "no_venv"

            proj_a.mkdir()
            proj_b.mkdir()
            no_venv.mkdir()

            scripts_a = proj_a / ".venv" / "Scripts"
            scripts_b = proj_b / ".venv" / "Scripts"
            scripts_a.mkdir(parents=True)
            scripts_b.mkdir(parents=True)

            dummy_pe = make_dummy_pe()
            (scripts_a / "python.exe").write_bytes(dummy_pe)
            (scripts_b / "python.exe").write_bytes(dummy_pe)

            activate_template = """
function global:deactivate ([switch]$NonDestructive) {
    Remove-Item env:VIRTUAL_ENV -ErrorAction SilentlyContinue
    if (-not $NonDestructive) {
        Remove-Item -Path function:deactivate
    }
}
deactivate -nondestructive
$env:VIRTUAL_ENV = "__VENV_DIR__"
"""
            (scripts_a / "Activate.ps1").write_text(activate_template.replace("__VENV_DIR__", str(proj_a / ".venv")), encoding="utf-8")
            (scripts_b / "Activate.ps1").write_text(activate_template.replace("__VENV_DIR__", str(proj_b / ".venv")), encoding="utf-8")

            sibling_envy = Path(sys.executable).parent / "envy.exe"
            envy_bin = str(sibling_envy) if sibling_envy.is_file() else "envy"

            hook_code = get_powershell_hook(envy_bin=envy_bin)
            hook_file = base / "envy_hook.ps1"
            hook_file.write_text(hook_code, encoding="utf-8")

            test_script = f"""
$ErrorActionPreference = 'Stop'
function global:prompt {{ 'PS> ' }}
. '{hook_file}'

function Capture-PromptOutput([scriptblock]$action) {{
    $out = & $action
    if ($out -is [System.Array]) {{
        $out = $out -join "`n"
    }}
    return ([string]$out).Trim()
}}

# 1. Start in no_venv -> no notification (returns exact baseline prompt)
Set-Location '{no_venv}'
$n0 = Capture-PromptOutput {{ prompt }}
if ($n0 -ne 'PS>') {{ throw "Expected baseline prompt in no_venv, got: $n0" }}

# 2. First activation in projA -> prompt output includes 'envy: venv loaded'
Set-Location '{proj_a}'
$n1 = Capture-PromptOutput {{ prompt }}
if (-not $n1.StartsWith("envy: venv loaded")) {{
    throw "Expected 'envy: venv loaded' prefix, got: $n1"
}}
if (-not $n1.EndsWith("PS>")) {{
    throw "Expected prompt to preserve chained 'PS>', got: $n1"
}}

# 3. Repeated prompt in projA -> no notification (returns exact baseline prompt)
$n2 = Capture-PromptOutput {{ prompt }}
if ($n2 -ne 'PS>') {{
    throw "Expected baseline prompt on repeated prompt, got: $n2"
}}

# 4. Switching directly from projA to projB -> prints 'envy: venv unloaded' then 'envy: venv loaded'
Set-Location '{proj_b}'
$n3 = (Capture-PromptOutput {{ prompt }}) -split "`r?`n"
if ($n3.Length -lt 3 -or $n3[0] -ne 'envy: venv unloaded' -or $n3[1] -ne 'envy: venv loaded' -or $n3[2] -ne 'PS>') {{
    throw "Expected unload and load followed by PS> on project switch, got: $($n3 -join ' | ')"
}}

# 5. Leaving projB to no_venv -> prints 'envy: venv unloaded' followed by PS>
Set-Location '{no_venv}'
$n4 = (Capture-PromptOutput {{ prompt }}) -split "`r?`n"
if ($n4.Length -ne 2 -or $n4[0] -ne 'envy: venv unloaded' -or $n4[1] -ne 'envy: venv loaded' -and $n4[1] -ne 'PS>') {{
    throw "Expected 'envy: venv unloaded' followed by PS>, got: $($n4 -join ' | ')"
}}

# 6. Manual activation -> not reported as envy-managed (no notifications)
$env:VIRTUAL_ENV = 'C:\\mock\\manual'
Set-Location '{proj_a}'
$n5 = Capture-PromptOutput {{ prompt }}
if ($n5 -ne 'PS>') {{
    throw "Expected baseline prompt for manually activated venv, got: $n5"
}}
Remove-Item env:VIRTUAL_ENV

Write-Output "ALL_POWERSHELL_NOTIFICATION_TESTS_PASSED"
"""
            test_file = base / "run_notif_test.ps1"
            test_file.write_text(test_script, encoding="utf-8")

            res = subprocess.run(
                [pwsh, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(test_file)],
                capture_output=True,
                text=True,
            )

            self.assertEqual(res.returncode, 0, f"PS failed with stderr: {res.stderr}\nstdout: {res.stdout}")
            self.assertIn("ALL_POWERSHELL_NOTIFICATION_TESTS_PASSED", res.stdout)

    @unittest.skipUnless(sys.platform == "win32", "PowerShell tests require Windows")
    def test_powershell_lifecycle_execution(self):
        """Execute the PowerShell hook in a real PowerShell subprocess to verify lifecycle."""
        pwsh = shutil.which("powershell.exe") or shutil.which("powershell")
        if not pwsh:
            self.skipTest("powershell executable not found")

        with tempfile.TemporaryDirectory() as td:
            base = Path(td).resolve()
            proj_a = base / "projA"
            proj_b = base / "projB"
            no_venv = base / "no_venv"

            proj_a.mkdir()
            proj_b.mkdir()
            no_venv.mkdir()

            scripts_a = proj_a / ".venv" / "Scripts"
            scripts_b = proj_b / ".venv" / "Scripts"
            scripts_a.mkdir(parents=True)
            scripts_b.mkdir(parents=True)

            dummy_pe = make_dummy_pe()
            (scripts_a / "python.exe").write_bytes(dummy_pe)
            (scripts_b / "python.exe").write_bytes(dummy_pe)

            # Create mock Activate.ps1 for A and B with accurate PATH tracking
            activate_template = """
function global:deactivate ([switch]$NonDestructive) {
    if (Test-Path Function:_OLD_VIRTUAL_PROMPT) {
        Copy-Item -Path Function:_OLD_VIRTUAL_PROMPT -Destination Function:prompt
        Remove-Item -Path Function:_OLD_VIRTUAL_PROMPT
    }
    if (Test-Path Env:_OLD_VIRTUAL_PATH) {
        $Env:PATH = $Env:_OLD_VIRTUAL_PATH
        Remove-Item Env:_OLD_VIRTUAL_PATH
    }
    Remove-Item env:VIRTUAL_ENV -ErrorAction SilentlyContinue
    if (-not $NonDestructive) {
        Remove-Item -Path function:deactivate
    }
}
deactivate -nondestructive
$env:VIRTUAL_ENV = "__VENV_DIR__"
$Env:_OLD_VIRTUAL_PATH = $Env:PATH
$Env:PATH = "__BIN_DIR__;$Env:PATH"
"""
            (scripts_a / "Activate.ps1").write_text(
                activate_template.replace("__VENV_DIR__", str(proj_a / ".venv")).replace("__BIN_DIR__", str(scripts_a)),
                encoding="utf-8",
            )
            (scripts_b / "Activate.ps1").write_text(
                activate_template.replace("__VENV_DIR__", str(proj_b / ".venv")).replace("__BIN_DIR__", str(scripts_b)),
                encoding="utf-8",
            )

            # Locate envy binary
            sibling_envy = Path(sys.executable).parent / "envy.exe"
            envy_bin = str(sibling_envy) if sibling_envy.is_file() else "envy"

            hook_code = get_powershell_hook(envy_bin=envy_bin)
            hook_file = base / "envy_hook.ps1"
            hook_file.write_text(hook_code, encoding="utf-8")

            # PS script to test all lifecycles
            test_script = f"""
$ErrorActionPreference = 'Stop'

# Initial baseline prompt
function global:prompt {{ 'PS> ' }}

# Load envy hook
. '{hook_file}'

# 1. Start in directory with no environment
Set-Location '{no_venv}'
$origPath = $env:PATH
$null = prompt
if ($env:VIRTUAL_ENV) {{ throw "Expected no VIRTUAL_ENV in no_venv" }}

# 2. Navigate to projA -> should auto-activate projA venv and update PATH
Set-Location '{proj_a}'
$null = prompt
if ($env:VIRTUAL_ENV -ne '{proj_a / ".venv"}') {{
    throw "Expected VIRTUAL_ENV to be projA, got: $env:VIRTUAL_ENV"
}}
if (-not $env:PATH.StartsWith('{scripts_a}')) {{
    throw "Expected PATH to start with projA scripts dir"
}}

# 3. Repeated prompt in projA -> idempotency check
$null = prompt
if ($env:VIRTUAL_ENV -ne '{proj_a / ".venv"}') {{
    throw "Expected VIRTUAL_ENV to remain projA on repeat prompt"
}}

# 4. Navigate directly to projB -> should deactivate projA and activate projB cleanly
Set-Location '{proj_b}'
$null = prompt
if ($env:VIRTUAL_ENV -ne '{proj_b / ".venv"}') {{
    throw "Expected VIRTUAL_ENV to switch to projB, got: $env:VIRTUAL_ENV"
}}
if (-not $env:PATH.StartsWith('{scripts_b}')) {{
    throw "Expected PATH to start with projB scripts dir"
}}
if ($env:PATH.Contains('{scripts_a}')) {{
    throw "projA path was leaked into projB PATH!"
}}

# 5. Navigate to no_venv -> should deactivate managed venv and restore exact original PATH
Set-Location '{no_venv}'
$null = prompt
if ($env:VIRTUAL_ENV) {{
    throw "Expected VIRTUAL_ENV to be cleared in no_venv, got: $env:VIRTUAL_ENV"
}}
if ($global:ENVY_MANAGED_VENV) {{
    throw "Expected ENVY_MANAGED_VENV to be null, got: $global:ENVY_MANAGED_VENV"
}}
if ($env:PATH -ne $origPath) {{
    throw "PATH was not restored to exact original after leaving projects!"
}}

# 6. Manual activation protection:
$manualVenv = 'C:\\MockManualVenv'
$env:VIRTUAL_ENV = $manualVenv
Set-Location '{no_venv}'
$null = prompt
if ($env:VIRTUAL_ENV -ne $manualVenv) {{
    throw "Manual venv was deactivated in no_venv!"
}}
Set-Location '{proj_a}'
$null = prompt
if ($env:VIRTUAL_ENV -ne $manualVenv) {{
    throw "Manual venv was overridden in projA!"
}}
Remove-Item env:VIRTUAL_ENV

# 7. Explicit deactivation test:
Set-Location '{proj_a}'
$null = prompt
if ($env:VIRTUAL_ENV -ne '{proj_a / ".venv"}') {{
    throw "Expected auto-activation in projA"
}}
deactivate
if ($env:VIRTUAL_ENV) {{ throw "Failed to manually deactivate" }}

# Next prompt in projA -> envy should NOT reactivate immediately
$null = prompt
if ($env:VIRTUAL_ENV) {{
    throw "Envy reactivated immediately after user called deactivate!"
}}

# Navigating away and back should re-enable auto-activation
Set-Location '{no_venv}'
$null = prompt
Set-Location '{proj_a}'
$null = prompt
if ($env:VIRTUAL_ENV -ne '{proj_a / ".venv"}') {{
    throw "Expected reactivation after re-entering projA"
}}

Write-Output "ALL_POWERSHELL_LIFECYCLE_TESTS_PASSED"
"""
            test_file = base / "run_test.ps1"
            test_file.write_text(test_script, encoding="utf-8")

            res = subprocess.run(
                [pwsh, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(test_file)],
                capture_output=True,
                text=True,
            )

            self.assertEqual(res.returncode, 0, f"PS failed with stderr: {res.stderr}\nstdout: {res.stdout}")
            self.assertIn("ALL_POWERSHELL_LIFECYCLE_TESTS_PASSED", res.stdout)

    @unittest.skipUnless(sys.platform == "win32", "PowerShell tests require Windows")
    def test_powershell_edge_cases(self):
        """Test prompt engine compatibility, exit code preservation, broken scripts, and nested projects."""
        pwsh = shutil.which("powershell.exe") or shutil.which("powershell")
        if not pwsh:
            self.skipTest("powershell executable not found")

        with tempfile.TemporaryDirectory() as td:
            base = Path(td).resolve()

            # Create nested project structure: parent/child/sub
            parent_dir = base / "parent"
            child_dir = parent_dir / "child"
            sibling_dir = parent_dir / "sibling"
            broken_dir = base / "broken"

            parent_dir.mkdir()
            child_dir.mkdir()
            sibling_dir.mkdir()
            broken_dir.mkdir()

            p_scripts = parent_dir / ".venv" / "Scripts"
            c_scripts = child_dir / ".venv" / "Scripts"
            b_scripts = broken_dir / ".venv" / "Scripts"
            p_scripts.mkdir(parents=True)
            c_scripts.mkdir(parents=True)
            b_scripts.mkdir(parents=True)

            dummy_pe = make_dummy_pe()
            (p_scripts / "python.exe").write_bytes(dummy_pe)
            (c_scripts / "python.exe").write_bytes(dummy_pe)
            (b_scripts / "python.exe").write_bytes(dummy_pe)

            simple_activate = """
function global:deactivate ([switch]$NonDestructive) {
    Remove-Item env:VIRTUAL_ENV -ErrorAction SilentlyContinue
    if (-not $NonDestructive) { Remove-Item -Path function:deactivate }
}
deactivate -nondestructive
$env:VIRTUAL_ENV = "__DIR__"
"""
            (p_scripts / "Activate.ps1").write_text(simple_activate.replace("__DIR__", str(parent_dir / ".venv")), encoding="utf-8")
            (c_scripts / "Activate.ps1").write_text(simple_activate.replace("__DIR__", str(child_dir / ".venv")), encoding="utf-8")
            # Broken Activate.ps1 throws syntax error
            (b_scripts / "Activate.ps1").write_text('throw "Intentional activation failure in test"', encoding="utf-8")

            sibling_envy = Path(sys.executable).parent / "envy.exe"
            envy_bin = str(sibling_envy) if sibling_envy.is_file() else "envy"

            hook_code = get_powershell_hook(envy_bin=envy_bin)
            hook_file = base / "envy_hook.ps1"
            hook_file.write_text(hook_code, encoding="utf-8")

            edge_test_script = f"""
$ErrorActionPreference = 'Stop'

# Test 1: Starship / Oh My Posh $LASTEXITCODE preservation
$global:capturedExitCode = $null
function global:prompt {{
    $global:capturedExitCode = $LASTEXITCODE
    'PS> '
}}

. '{hook_file}'

# Trigger an exit code 42
powershell -Command 'exit 42'
$null = prompt
if ($global:capturedExitCode -ne 42) {{
    throw "Expected prompt to preserve LASTEXITCODE=42, but saw: $global:capturedExitCode"
}}

# Test 2: Nested project hierarchy transitions
Set-Location '{parent_dir}'
$null = prompt
if ($env:VIRTUAL_ENV -ne '{parent_dir / ".venv"}') {{
    throw "Failed to activate parent venv"
}}

# Sibling folder has no direct venv -> should resolve parent venv without reactivation
Set-Location '{sibling_dir}'
$null = prompt
if ($env:VIRTUAL_ENV -ne '{parent_dir / ".venv"}') {{
    throw "Sibling folder did not retain parent venv"
}}

# Child folder has its own venv -> should switch to child venv
Set-Location '{child_dir}'
$null = prompt
if ($env:VIRTUAL_ENV -ne '{child_dir / ".venv"}') {{
    throw "Failed to activate child venv"
}}

# Return to parent folder -> should switch back to parent venv
Set-Location '{parent_dir}'
$null = prompt
if ($env:VIRTUAL_ENV -ne '{parent_dir / ".venv"}') {{
    throw "Failed to reactivate parent venv"
}}

# Test 3: Broken Activate.ps1 does not crash prompt or loop endlessly
Set-Location '{broken_dir}'
$null = prompt  # Should log warning, but not throw terminating error
if ($env:VIRTUAL_ENV) {{
    throw "Broken venv should not have set VIRTUAL_ENV"
}}
if ($null -eq $global:ENVY_FAILED_DIR) {{
    throw "Failed directory was not recorded"
}}
# Second prompt in broken dir should not re-attempt throwing
$null = prompt

Write-Output "ALL_EDGE_CASE_TESTS_PASSED"
"""
            edge_test_file = base / "run_edge_test.ps1"
            edge_test_file.write_text(edge_test_script, encoding="utf-8")

            res = subprocess.run(
                [pwsh, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(edge_test_file)],
                capture_output=True,
                text=True,
            )

            self.assertEqual(res.returncode, 0, f"Edge tests failed with stderr: {res.stderr}\nstdout: {res.stdout}")
            self.assertIn("ALL_EDGE_CASE_TESTS_PASSED", res.stdout)


if __name__ == "__main__":
    unittest.main()
