"""Integration and edge-case tests for Bash hook in WSL Ubuntu / POSIX."""

from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

from envy.shells.bash import get_bash_hook


def is_wsl_available() -> bool:
    """Check if WSL is available on this Windows host."""
    if sys.platform != "win32":
        return shutil.which("bash") is not None
    wsl = shutil.which("wsl.exe") or shutil.which("wsl")
    if not wsl:
        return False
    try:
        res = subprocess.run([wsl, "bash", "-c", "echo ok"], capture_output=True, text=True, timeout=5)
        return res.returncode == 0 and "ok" in res.stdout
    except (subprocess.SubprocessError, OSError):
        return False


class TestBashHook(unittest.TestCase):
    def test_hook_generation_contains_key_contracts(self):
        hook = get_bash_hook(envy_bin="/custom/bin/envy")
        self.assertIn("/custom/bin/envy", hook)
        self.assertIn("__ENVY_HOOKED", hook)
        self.assertIn("__envy_hook", hook)
        self.assertIn("PROMPT_COMMAND", hook)
        self.assertIn("ENVY_MANAGED_VENV", hook)
        self.assertIn("ENVY_DEACTIVATED_DIR", hook)
        self.assertIn("ENVY_FAILED_DIR", hook)
        self.assertIn("bin/activate", hook)
        self.assertIn('retval="$?"', hook)
        self.assertIn('return "${retval}"', hook)
        self.assertIn('echo "envy: venv loaded"', hook)
        self.assertIn('echo "envy: venv unloaded"', hook)

    @unittest.skipUnless(is_wsl_available(), "WSL Bash is required for this test")
    def test_wsl_bash_notifications(self):
        """Test minimal activation/deactivation notifications in Bash."""
        wsl = shutil.which("wsl.exe") or shutil.which("wsl") or "bash"
        repo_wsl_path = "/mnt/c/Users/fasiu/envy"

        script = """
set -e
TEST_DIR=$(mktemp -d /tmp/envy_bash_notif_XXXXXX)
trap 'rm -rf "$TEST_DIR"' EXIT

PROJ_A="$TEST_DIR/projA"
PROJ_B="$TEST_DIR/projB"
NO_VENV="$TEST_DIR/no_venv"
mkdir -p "$PROJ_A/.venv/bin" "$PROJ_B/.venv/bin" "$NO_VENV"

create_activate() {
    local venv_dir="$1"
    local bin_dir="$venv_dir/bin"
    cat << 'EOF' > "$bin_dir/activate"
deactivate () {
    unset VIRTUAL_ENV
    if [ ! "${1-}" = "nondestructive" ] ; then
        unset -f deactivate
    fi
}
deactivate nondestructive
VIRTUAL_ENV="__VENV_DIR__"
export VIRTUAL_ENV
EOF
    sed -i "s|__VENV_DIR__|$venv_dir|g" "$bin_dir/activate"
    ln -s /usr/bin/python3 "$bin_dir/python"
}

create_activate "$PROJ_A/.venv"
create_activate "$PROJ_B/.venv"

cat << 'EOF' > "$TEST_DIR/envy_shim"
#!/bin/bash
exec /usr/bin/python3 -m envy.cli "$@"
EOF
chmod +x "$TEST_DIR/envy_shim"

export PYTHONPATH="__REPO_PATH__/src"
export ENVY_BIN="$TEST_DIR/envy_shim"

HOOK_CODE=$(/usr/bin/python3 -m envy.cli init bash)
eval "$HOOK_CODE"

OUT_FILE="$TEST_DIR/hook_output"

# 1. Start in no_venv -> no notification
cd "$NO_VENV"
__envy_hook > "$OUT_FILE"
out0=$(cat "$OUT_FILE")
if [[ -n "$out0" ]]; then echo "Expected no notification in no_venv, got: $out0" >&2; exit 1; fi

# 2. First activation in projA -> exactly 'envy: venv loaded'
cd "$PROJ_A"
__envy_hook > "$OUT_FILE"
out1=$(cat "$OUT_FILE")
if [[ "$out1" != "envy: venv loaded" ]]; then echo "Expected 'envy: venv loaded', got: $out1" >&2; exit 1; fi

# 3. Repeated prompt in projA -> no notification
__envy_hook > "$OUT_FILE"
out2=$(cat "$OUT_FILE")
if [[ -n "$out2" ]]; then echo "Expected no notification on repeat, got: $out2" >&2; exit 1; fi

# 4. Switching directly from projA to projB -> prints 'envy: venv unloaded' then 'envy: venv loaded'
cd "$PROJ_B"
__envy_hook > "$OUT_FILE"
out3=$(cat "$OUT_FILE")
expected=$'envy: venv unloaded\nenvy: venv loaded'
if [[ "$out3" != "$expected" ]]; then
    echo "Expected unload and load on project switch, got: '$out3'" >&2
    exit 1
fi

# 5. Leaving projB to no_venv -> prints 'envy: venv unloaded'
cd "$NO_VENV"
__envy_hook > "$OUT_FILE"
out4=$(cat "$OUT_FILE")
if [[ "$out4" != "envy: venv unloaded" ]]; then echo "Expected 'envy: venv unloaded', got: $out4" >&2; exit 1; fi

# 6. Manual activation -> not reported as envy-managed (no notification)
export VIRTUAL_ENV="/tmp/manual_mock"
cd "$PROJ_A"
__envy_hook > "$OUT_FILE"
out5=$(cat "$OUT_FILE")
if [[ -n "$out5" ]]; then echo "Expected no notification for manual venv, got: $out5" >&2; exit 1; fi
unset VIRTUAL_ENV

echo "ALL_WSL_BASH_NOTIFICATION_TESTS_PASSED"
""".replace("__REPO_PATH__", repo_wsl_path).replace("\r\n", "\n")

        res = subprocess.run([wsl, "bash"], input=script.encode("utf-8"), capture_output=True)
        self.assertEqual(res.returncode, 0, f"Failed: {res.stderr.decode('utf-8')}\nstdout: {res.stdout.decode('utf-8')}")
        self.assertIn("ALL_WSL_BASH_NOTIFICATION_TESTS_PASSED", res.stdout.decode("utf-8"))

    @unittest.skipUnless(is_wsl_available(), "WSL Bash is required for this test")
    def test_prompt_command_string_and_array_preservation(self):
        """Verify PROMPT_COMMAND preservation in both string and array formats."""
        wsl = shutil.which("wsl.exe") or shutil.which("wsl") or "bash"
        hook_code = get_bash_hook(envy_bin="envy")

        script = """
__HOOK_CODE__

# Test 1: String format PROMPT_COMMAND
PROMPT_COMMAND="my_prev_cmd1; my_prev_cmd2"
__envy_hooked="" # allow re-evaluating registration block
if declare -p PROMPT_COMMAND 2>/dev/null | grep -q 'declare -a'; then
    _envy_already_hooked=0
    for item in "${PROMPT_COMMAND[@]}"; do
        if [[ "$item" == "__envy_hook" ]]; then _envy_already_hooked=1; break; fi
    done
    if [[ $_envy_already_hooked -eq 0 ]]; then PROMPT_COMMAND=("__envy_hook" "${PROMPT_COMMAND[@]}"); fi
else
    if [[ ";${PROMPT_COMMAND:-};" != *";__envy_hook;"* ]]; then
        PROMPT_COMMAND="__envy_hook;${PROMPT_COMMAND}"
    fi
fi

if [[ "$PROMPT_COMMAND" != "__envy_hook;my_prev_cmd1; my_prev_cmd2" ]]; then
    echo "String preservation failed: $PROMPT_COMMAND" >&2
    exit 1
fi

# Test 2: Array format PROMPT_COMMAND
PROMPT_COMMAND=("cmd_one" "cmd_two")
if declare -p PROMPT_COMMAND 2>/dev/null | grep -q 'declare -a'; then
    _envy_already_hooked=0
    for item in "${PROMPT_COMMAND[@]}"; do
        if [[ "$item" == "__envy_hook" ]]; then _envy_already_hooked=1; break; fi
    done
    if [[ $_envy_already_hooked -eq 0 ]]; then PROMPT_COMMAND=("__envy_hook" "${PROMPT_COMMAND[@]}"); fi
else
    if [[ ";${PROMPT_COMMAND:-};" != *";__envy_hook;"* ]]; then
        PROMPT_COMMAND="__envy_hook;${PROMPT_COMMAND}"
    fi
fi

if [[ "${#PROMPT_COMMAND[@]}" -ne 3 || "${PROMPT_COMMAND[0]}" != "__envy_hook" || "${PROMPT_COMMAND[1]}" != "cmd_one" || "${PROMPT_COMMAND[2]}" != "cmd_two" ]]; then
    echo "Array preservation failed: ${PROMPT_COMMAND[*]}" >&2
    exit 1
fi

echo "PROMPT_COMMAND_PRESERVATION_PASSED"
""".replace("__HOOK_CODE__", hook_code).replace("\r\n", "\n")

        res = subprocess.run([wsl, "bash"], input=script.encode("utf-8"), capture_output=True)
        self.assertEqual(res.returncode, 0, f"Failed: {res.stderr.decode('utf-8')}")
        self.assertIn("PROMPT_COMMAND_PRESERVATION_PASSED", res.stdout.decode("utf-8"))

    @unittest.skipUnless(is_wsl_available(), "WSL Bash is required for this test")
    def test_wsl_exit_status_preservation(self):
        """Verify that __envy_hook preserves the exit code of previous commands."""
        wsl = shutil.which("wsl.exe") or shutil.which("wsl") or "bash"
        hook_code = get_bash_hook(envy_bin="envy")

        script = """
__HOOK_CODE__

# Trigger an exit code 42
(exit 42)
__envy_hook
saved_code=$?

if [[ $saved_code -ne 42 ]]; then
    echo "Expected exit code 42, got: $saved_code" >&2
    exit 1
fi

echo "EXIT_CODE_PRESERVATION_PASSED"
""".replace("__HOOK_CODE__", hook_code).replace("\r\n", "\n")

        res = subprocess.run([wsl, "bash"], input=script.encode("utf-8"), capture_output=True)
        self.assertEqual(res.returncode, 0, f"Failed: {res.stderr.decode('utf-8')}")
        self.assertIn("EXIT_CODE_PRESERVATION_PASSED", res.stdout.decode("utf-8"))

    @unittest.skipUnless(is_wsl_available(), "WSL Bash is required for this test")
    def test_wsl_bash_lifecycle_execution(self):
        """Execute the Bash hook in a real WSL Ubuntu Bash session to verify full lifecycle."""
        wsl = shutil.which("wsl.exe") or shutil.which("wsl") or "bash"
        repo_wsl_path = "/mnt/c/Users/fasiu/envy"

        script = """
set -e
TEST_DIR=$(mktemp -d /tmp/envy_bash_test_XXXXXX)
trap 'rm -rf "$TEST_DIR"' EXIT

PROJ_A="$TEST_DIR/projA"
PROJ_B="$TEST_DIR/projB"
NO_VENV="$TEST_DIR/no_venv"
mkdir -p "$PROJ_A/.venv/bin" "$PROJ_B/.venv/bin" "$NO_VENV"

# Helper to create valid Linux activate script
create_activate() {
    local venv_dir="$1"
    local bin_dir="$venv_dir/bin"
    cat << 'EOF' > "$bin_dir/activate"
deactivate () {
    if [ -n "${_OLD_VIRTUAL_PATH:-}" ] ; then
        PATH="${_OLD_VIRTUAL_PATH}"
        export PATH
        unset _OLD_VIRTUAL_PATH
    fi
    if [ -n "${_OLD_VIRTUAL_PYTHONHOME:-}" ] ; then
        PYTHONHOME="${_OLD_VIRTUAL_PYTHONHOME}"
        export PYTHONHOME
        unset _OLD_VIRTUAL_PYTHONHOME
    fi
    if [ -n "${_OLD_VIRTUAL_PS1:-}" ] ; then
        PS1="${_OLD_VIRTUAL_PS1}"
        export PS1
        unset _OLD_VIRTUAL_PS1
    fi
    unset VIRTUAL_ENV
    unset VIRTUAL_ENV_PROMPT
    if [ ! "${1-}" = "nondestructive" ] ; then
        unset -f deactivate
    fi
}
deactivate nondestructive
VIRTUAL_ENV="__VENV_DIR__"
export VIRTUAL_ENV
_OLD_VIRTUAL_PATH="$PATH"
PATH="__BIN_DIR__:$PATH"
export PATH
EOF
    sed -i "s|__VENV_DIR__|$venv_dir|g" "$bin_dir/activate"
    sed -i "s|__BIN_DIR__|$bin_dir|g" "$bin_dir/activate"

    # Create native Linux interpreter symlink pointing to system python
    ln -s /usr/bin/python3 "$bin_dir/python"
}

create_activate "$PROJ_A/.venv"
create_activate "$PROJ_B/.venv"

# Create envy runner shim pointing to the shared python core
cat << 'EOF' > "$TEST_DIR/envy_shim"
#!/bin/bash
exec /usr/bin/python3 -m envy.cli "$@"
EOF
chmod +x "$TEST_DIR/envy_shim"

export PYTHONPATH="__REPO_PATH__/src"
export ENVY_BIN="$TEST_DIR/envy_shim"

# Generate and source bash hook
HOOK_CODE=$(/usr/bin/python3 -m envy.cli init bash)
eval "$HOOK_CODE"

ORIG_PATH="$PATH"

# 1. Start in no_venv
cd "$NO_VENV"
__envy_hook >/dev/null
if [[ -n "$VIRTUAL_ENV" ]]; then echo "VIRTUAL_ENV should be empty in no_venv" >&2; exit 1; fi

# 2. Navigate to projA -> activates
cd "$PROJ_A"
__envy_hook >/dev/null
if [[ "$VIRTUAL_ENV" != "$PROJ_A/.venv" ]]; then echo "Expected projA venv, got: $VIRTUAL_ENV" >&2; exit 1; fi
if [[ "$ENVY_MANAGED_VENV" != "$PROJ_A/.venv" ]]; then echo "Expected ENVY_MANAGED_VENV projA, got: $ENVY_MANAGED_VENV" >&2; exit 1; fi

# 3. Repeated prompt -> idempotent
__envy_hook >/dev/null
if [[ "$VIRTUAL_ENV" != "$PROJ_A/.venv" ]]; then echo "Expected projA venv to persist" >&2; exit 1; fi

# 4. Navigate directly to projB -> switches cleanly
cd "$PROJ_B"
__envy_hook >/dev/null
if [[ "$VIRTUAL_ENV" != "$PROJ_B/.venv" ]]; then echo "Expected projB venv, got: $VIRTUAL_ENV" >&2; exit 1; fi

# 5. Navigate to no_venv -> deactivates and restores PATH
cd "$NO_VENV"
__envy_hook >/dev/null
if [[ -n "$VIRTUAL_ENV" ]]; then echo "Expected no VIRTUAL_ENV in no_venv" >&2; exit 1; fi
if [[ "$PATH" != "$ORIG_PATH" ]]; then echo "PATH not restored to exact original" >&2; exit 1; fi

# 6. Manual activation protection
export VIRTUAL_ENV="/tmp/manual_mock_venv"
cd "$PROJ_A"
__envy_hook >/dev/null
if [[ "$VIRTUAL_ENV" != "/tmp/manual_mock_venv" ]]; then echo "Manual venv was overridden!" >&2; exit 1; fi
unset VIRTUAL_ENV

# 7. Explicit deactivate in managed project
cd "$PROJ_A"
__envy_hook >/dev/null
if [[ "$VIRTUAL_ENV" != "$PROJ_A/.venv" ]]; then echo "Failed to auto-activate projA" >&2; exit 1; fi
deactivate
if [[ -n "$VIRTUAL_ENV" ]]; then echo "Failed to deactivate" >&2; exit 1; fi

# Prompt runs again in projA -> must NOT immediately reactivate
__envy_hook >/dev/null
if [[ -n "$VIRTUAL_ENV" ]]; then echo "Envy reactivated immediately after deactivate!" >&2; exit 1; fi

# Leaving and returning re-activates
cd "$NO_VENV"
__envy_hook >/dev/null
cd "$PROJ_A"
__envy_hook >/dev/null
if [[ "$VIRTUAL_ENV" != "$PROJ_A/.venv" ]]; then echo "Failed to re-activate after re-entry" >&2; exit 1; fi

# 8. Switching from valid parent into child project with an invalid/corrupted environment
CORRUPTED_CHILD="$PROJ_A/corrupted_child"
mkdir -p "$CORRUPTED_CHILD/.venv/bin"
touch "$CORRUPTED_CHILD/.venv/bin/activate"
touch "$CORRUPTED_CHILD/.venv/bin/python" # 0 bytes

cd "$CORRUPTED_CHILD"
__envy_hook >/dev/null
if [[ -n "$VIRTUAL_ENV" ]]; then
    echo "Expected VIRTUAL_ENV to be unset in corrupted child, got: $VIRTUAL_ENV" >&2
    exit 1
fi
if [[ -n "$ENVY_MANAGED_VENV" ]]; then
    echo "Expected ENVY_MANAGED_VENV to be unset in corrupted child, got: $ENVY_MANAGED_VENV" >&2
    exit 1
fi

# Return to parent: must restore parent venv
cd "$PROJ_A"
__envy_hook >/dev/null
if [[ "$VIRTUAL_ENV" != "$PROJ_A/.venv" ]]; then
    echo "Failed to re-activate parent venv after returning from corrupted child" >&2
    exit 1
fi

echo "ALL_WSL_BASH_LIFECYCLE_TESTS_PASSED"
""".replace("__REPO_PATH__", repo_wsl_path).replace("\r\n", "\n")

        res = subprocess.run([wsl, "bash"], input=script.encode("utf-8"), capture_output=True)
        self.assertEqual(res.returncode, 0, f"Failed: {res.stderr.decode('utf-8')}\nstdout: {res.stdout.decode('utf-8')}")
        self.assertIn("ALL_WSL_BASH_LIFECYCLE_TESTS_PASSED", res.stdout.decode("utf-8"))

    @unittest.skipUnless(is_wsl_available(), "WSL Bash is required for this test")
    def test_wsl_cross_activation_protection_and_precedence(self):
        """Verify that WSL never activates Windows environments, and platform precedence works."""
        wsl = shutil.which("wsl.exe") or shutil.which("wsl") or "bash"
        repo_wsl_path = "/mnt/c/Users/fasiu/envy"

        script = """
set -e
TEST_DIR=$(mktemp -d /tmp/envy_cross_test_XXXXXX)
trap 'rm -rf "$TEST_DIR"' EXIT

export PYTHONPATH="__REPO_PATH__/src"

# 1. Project with Windows-only venv (Scripts/python.exe, Scripts/activate)
WIN_PROJ="$TEST_DIR/win_proj"
mkdir -p "$WIN_PROJ/.venv/Scripts"
touch "$WIN_PROJ/.venv/Scripts/activate"
touch "$WIN_PROJ/.venv/Scripts/python.exe"

# Verify resolve exits nonzero and outputs nothing on stdout
RESOLVED_WIN=$(/usr/bin/python3 -m envy.cli resolve "$WIN_PROJ" --shell bash 2>/dev/null || true)
if [[ -n "$RESOLVED_WIN" ]]; then
    echo "CROSS-ACTIVATION LEAK! WSL Envy resolved a Windows venv: $RESOLVED_WIN" >&2
    exit 1
fi
if /usr/bin/python3 -m envy.cli resolve "$WIN_PROJ" --shell bash >/dev/null 2>&1; then
    echo "Expected nonzero exit code when resolving Windows venv on Linux!" >&2
    exit 1
fi

# 2. Project with fake Linux venv where bin/python is a Windows PE binary (MZ header)
FAKE_PROJ="$TEST_DIR/fake_proj"
mkdir -p "$FAKE_PROJ/.venv/bin"
touch "$FAKE_PROJ/.venv/bin/activate"
printf 'MZ\\x90\\x00fake_windows_binary' > "$FAKE_PROJ/.venv/bin/python"

RESOLVED_FAKE=$(/usr/bin/python3 -m envy.cli resolve "$FAKE_PROJ" --shell bash 2>/dev/null || true)
if [[ -n "$RESOLVED_FAKE" ]]; then
    echo "NON-NATIVE INTERPRETER LEAK! WSL Envy resolved non-ELF python: $RESOLVED_FAKE" >&2
    exit 1
fi
if /usr/bin/python3 -m envy.cli resolve "$FAKE_PROJ" --shell bash >/dev/null 2>&1; then
    echo "Expected nonzero exit code when resolving fake binary venv on Linux!" >&2
    exit 1
fi

# 3. Project with dual environments: .venv (Windows) and .venv-wsl (Linux ELF)
DUAL_PROJ="$TEST_DIR/dual_proj"
mkdir -p "$DUAL_PROJ/.venv/Scripts"
touch "$DUAL_PROJ/.venv/Scripts/Activate.ps1"
touch "$DUAL_PROJ/.venv/Scripts/python.exe"

mkdir -p "$DUAL_PROJ/.venv-wsl/bin"
touch "$DUAL_PROJ/.venv-wsl/bin/activate"
ln -s /usr/bin/python3 "$DUAL_PROJ/.venv-wsl/bin/python"

RESOLVED_DUAL=$(/usr/bin/python3 -m envy.cli resolve "$DUAL_PROJ" --shell bash)
if [[ "$RESOLVED_DUAL" != "$DUAL_PROJ/.venv-wsl" ]]; then
    echo "Deterministic precedence failed! Expected .venv-wsl, got: $RESOLVED_DUAL" >&2
    exit 1
fi

echo "ALL_CROSS_ACTIVATION_AND_PRECEDENCE_TESTS_PASSED"
""".replace("__REPO_PATH__", repo_wsl_path).replace("\r\n", "\n")

        res = subprocess.run([wsl, "bash"], input=script.encode("utf-8"), capture_output=True)
        self.assertEqual(res.returncode, 0, f"Failed: {res.stderr.decode('utf-8')}\nstdout: {res.stdout.decode('utf-8')}")
        self.assertIn("ALL_CROSS_ACTIVATION_AND_PRECEDENCE_TESTS_PASSED", res.stdout.decode("utf-8"))

    @unittest.skipUnless(is_wsl_available(), "WSL Bash is required for this test")
    def test_wsl_bash_resolver_diagnostic_visibility(self):
        """Verify resolver diagnostics visibility, idempotency, and clean stdout in Bash."""
        wsl = shutil.which("wsl.exe") or shutil.which("wsl") or "bash"
        repo_wsl_path = "/mnt/c/Users/fasiu/envy"

        script = """
set -e
TEST_DIR=$(mktemp -d /tmp/envy_bash_diag_XXXXXX)
trap 'rm -rf "$TEST_DIR"' EXIT

VALID_PROJ="$TEST_DIR/valid_proj"
INVALID_INTERP_PROJ="$TEST_DIR/invalid_interp_proj"
MISSING_ACT_PROJ="$TEST_DIR/missing_act_proj"
PARENT_PROJ="$TEST_DIR/parent_proj"
BROKEN_CHILD="$PARENT_PROJ/broken_child"
NO_VENV="$TEST_DIR/no_venv"

mkdir -p "$VALID_PROJ/.venv/bin" "$INVALID_INTERP_PROJ/.venv/bin" "$MISSING_ACT_PROJ/.venv/bin"
mkdir -p "$PARENT_PROJ/.venv/bin" "$BROKEN_CHILD/.venv/bin" "$NO_VENV"

create_activate() {
    local venv_dir="$1"
    local bin_dir="$venv_dir/bin"
    cat << 'EOF' > "$bin_dir/activate"
deactivate () {
    unset VIRTUAL_ENV
    if [ ! "${1-}" = "nondestructive" ] ; then
        unset -f deactivate
    fi
}
deactivate nondestructive
VIRTUAL_ENV="__VENV_DIR__"
export VIRTUAL_ENV
EOF
    sed -i "s|__VENV_DIR__|$venv_dir|g" "$bin_dir/activate"
    ln -s /usr/bin/python3 "$bin_dir/python"
}

# 1. Valid environment
create_activate "$VALID_PROJ/.venv"

# 2. Invalid interpreter: 0-byte python binary
create_activate "$INVALID_INTERP_PROJ/.venv"
rm -f "$INVALID_INTERP_PROJ/.venv/bin/python"
touch "$INVALID_INTERP_PROJ/.venv/bin/python"

# 3. Missing activation script
ln -s /usr/bin/python3 "$MISSING_ACT_PROJ/.venv/bin/python"

# 4. Valid parent project with broken child project (0-byte interpreter)
create_activate "$PARENT_PROJ/.venv"
create_activate "$BROKEN_CHILD/.venv"
rm -f "$BROKEN_CHILD/.venv/bin/python"
touch "$BROKEN_CHILD/.venv/bin/python"

cat << 'EOF' > "$TEST_DIR/envy_shim"
#!/bin/bash
exec /usr/bin/python3 -m envy.cli "$@"
EOF
chmod +x "$TEST_DIR/envy_shim"

export PYTHONPATH="__REPO_PATH__/src"
export ENVY_BIN="$TEST_DIR/envy_shim"

HOOK_CODE=$(/usr/bin/python3 -m envy.cli init bash)
eval "$HOOK_CODE"

OUT_FILE="$TEST_DIR/stdout.txt"
ERR_FILE="$TEST_DIR/stderr.txt"

# --- 1. Entering a project with a valid environment ---
cd "$VALID_PROJ"
__envy_hook > "$OUT_FILE" 2> "$ERR_FILE"
out=$(cat "$OUT_FILE")
err=$(cat "$ERR_FILE")
if [[ "$out" != "envy: venv loaded" ]]; then
    echo "Expected 'envy: venv loaded' in stdout, got: '$out'" >&2
    exit 1
fi
if [[ -n "$err" ]]; then
    echo "Expected NO stderr diagnostics for valid environment, got: '$err'" >&2
    exit 1
fi
if [[ "$VIRTUAL_ENV" != "$VALID_PROJ/.venv" ]]; then
    echo "Expected VIRTUAL_ENV to be set to valid venv" >&2
    exit 1
fi

# --- 2. Entering a project with an invalid interpreter ---
cd "$INVALID_INTERP_PROJ"
__envy_hook > "$OUT_FILE" 2> "$ERR_FILE"
out=$(cat "$OUT_FILE")
err=$(cat "$ERR_FILE")
if [[ "$out" != "envy: venv unloaded" ]]; then
    echo "Expected previous venv unloaded in stdout, got: '$out'" >&2
    exit 1
fi
if [[ -n "$VIRTUAL_ENV" ]]; then
    echo "Expected VIRTUAL_ENV to be unset for invalid interpreter" >&2
    exit 1
fi
if [[ "$err" != *"envy: error:"* || "$err" != *"is empty"* ]]; then
    echo "Expected 'is empty' diagnostic on stderr, got: '$err'" >&2
    exit 1
fi

# --- 3. Repeated prompt in the broken project -> NO repeated diagnostics ---
__envy_hook > "$OUT_FILE" 2> "$ERR_FILE"
out=$(cat "$OUT_FILE")
err=$(cat "$ERR_FILE")
if [[ -n "$out" || -n "$err" ]]; then
    echo "Expected no stdout or stderr on repeat prompt in invalid project, got out='$out', err='$err'" >&2
    exit 1
fi

# --- 4. Entering a project with a missing activation script ---
cd "$MISSING_ACT_PROJ"
__envy_hook > "$OUT_FILE" 2> "$ERR_FILE"
out=$(cat "$OUT_FILE")
err=$(cat "$ERR_FILE")
if [[ "$err" != *"envy: error:"* || "$err" != *"activation script"* ]]; then
    echo "Expected missing activation script diagnostic on stderr, got: '$err'" >&2
    exit 1
fi
if [[ -n "$VIRTUAL_ENV" ]]; then
    echo "Expected VIRTUAL_ENV to be unset for missing activation script" >&2
    exit 1
fi

# --- 5. Switching from valid parent into broken child ---
cd "$PARENT_PROJ"
__envy_hook > "$OUT_FILE" 2> "$ERR_FILE"
if [[ "$VIRTUAL_ENV" != "$PARENT_PROJ/.venv" ]]; then
    echo "Expected parent venv activated" >&2
    exit 1
fi

cd "$BROKEN_CHILD"
__envy_hook > "$OUT_FILE" 2> "$ERR_FILE"
out=$(cat "$OUT_FILE")
err=$(cat "$ERR_FILE")
if [[ "$out" != "envy: venv unloaded" ]]; then
    echo "Expected parent venv unloaded when entering broken child, got: '$out'" >&2
    exit 1
fi
if [[ -n "$VIRTUAL_ENV" ]]; then
    echo "Expected VIRTUAL_ENV to be unset in broken child, got: '$VIRTUAL_ENV'" >&2
    exit 1
fi
if [[ "$err" != *"envy: error:"* || "$err" != *"is empty"* ]]; then
    echo "Expected child diagnostic on stderr, got: '$err'" >&2
    exit 1
fi

# Repeat prompt in broken child -> no repeated diagnostic
__envy_hook > "$OUT_FILE" 2> "$ERR_FILE"
out=$(cat "$OUT_FILE")
err=$(cat "$ERR_FILE")
if [[ -n "$out" || -n "$err" ]]; then
    echo "Expected no repeat diagnostic in broken child, got out='$out', err='$err'" >&2
    exit 1
fi

# --- 6. Repairing the environment and re-entering the project ---
rm -f "$INVALID_INTERP_PROJ/.venv/bin/python"
ln -s /usr/bin/python3 "$INVALID_INTERP_PROJ/.venv/bin/python"

cd "$NO_VENV"
__envy_hook >/dev/null 2>&1

cd "$INVALID_INTERP_PROJ"
__envy_hook > "$OUT_FILE" 2> "$ERR_FILE"
out=$(cat "$OUT_FILE")
err=$(cat "$ERR_FILE")
if [[ "$out" != "envy: venv loaded" ]]; then
    echo "Expected 'envy: venv loaded' after repairing venv, got: '$out'" >&2
    exit 1
fi
if [[ -n "$err" ]]; then
    echo "Expected NO stderr diagnostic after repairing venv, got: '$err'" >&2
    exit 1
fi
if [[ "$VIRTUAL_ENV" != "$INVALID_INTERP_PROJ/.venv" ]]; then
    echo "Expected repaired venv to be active, got: '$VIRTUAL_ENV'" >&2
    exit 1
fi

echo "ALL_BASH_DIAGNOSTIC_VISIBILITY_TESTS_PASSED"
""".replace("__REPO_PATH__", repo_wsl_path).replace("\r\n", "\n")

        res = subprocess.run([wsl, "bash"], input=script.encode("utf-8"), capture_output=True)
        self.assertEqual(res.returncode, 0, f"Failed: {res.stderr.decode('utf-8')}\nstdout: {res.stdout.decode('utf-8')}")
        self.assertIn("ALL_BASH_DIAGNOSTIC_VISIBILITY_TESTS_PASSED", res.stdout.decode("utf-8"))

    @unittest.skipUnless(is_wsl_available(), "WSL Bash is required for this test")
    def test_wsl_bash_lifecycle_reliability(self):
        """Verify activation failure recovery, rollback, environment deletion while active, and PATH stability in Bash."""
        wsl = shutil.which("wsl.exe") or shutil.which("wsl") or "bash"
        repo_wsl_path = "/mnt/c/Users/fasiu/envy"

        script = """
set -e
TEST_DIR=$(mktemp -d /tmp/envy_bash_lifecycle_XXXXXX)
trap 'rm -rf "$TEST_DIR"' EXIT

PROJ_A="$TEST_DIR/projA"
PROJ_B="$TEST_DIR/projB"
BROKEN_PROJ="$TEST_DIR/broken_proj"
NO_VENV="$TEST_DIR/no_venv"

mkdir -p "$PROJ_A/.venv/bin" "$PROJ_B/.venv/bin" "$BROKEN_PROJ/.venv/bin" "$NO_VENV"

create_valid_activate() {
    local venv_dir="$1"
    local bin_dir="$venv_dir/bin"
    cat << 'EOF' > "$bin_dir/activate"
deactivate () {
    if [ -n "${_OLD_VIRTUAL_PATH:-}" ] ; then
        PATH="${_OLD_VIRTUAL_PATH}"
        export PATH
        unset _OLD_VIRTUAL_PATH
    fi
    unset VIRTUAL_ENV
    unset VIRTUAL_ENV_PROMPT
    if [ ! "${1-}" = "nondestructive" ] ; then
        unset -f deactivate
    fi
}
deactivate nondestructive
VIRTUAL_ENV="__VENV_DIR__"
export VIRTUAL_ENV
_OLD_VIRTUAL_PATH="$PATH"
PATH="__BIN_DIR__:$PATH"
export PATH
EOF
    sed -i "s|__VENV_DIR__|$venv_dir|g" "$bin_dir/activate"
    sed -i "s|__BIN_DIR__|$bin_dir|g" "$bin_dir/activate"
    ln -sf /usr/bin/python3 "$bin_dir/python"
}

create_failing_activate() {
    local venv_dir="$1"
    local bin_dir="$venv_dir/bin"
    cat << 'EOF' > "$bin_dir/activate"
VIRTUAL_ENV="__VENV_DIR__"
export VIRTUAL_ENV
_OLD_VIRTUAL_PATH="$PATH"
PATH="__BIN_DIR__:$PATH"
export PATH
return 1 # simulated activation script crash halfway through
EOF
    sed -i "s|__VENV_DIR__|$venv_dir|g" "$bin_dir/activate"
    sed -i "s|__BIN_DIR__|$bin_dir|g" "$bin_dir/activate"
    ln -sf /usr/bin/python3 "$bin_dir/python"
}

create_valid_activate "$PROJ_A/.venv"
create_valid_activate "$PROJ_B/.venv"
create_failing_activate "$BROKEN_PROJ/.venv"

cat << 'EOF' > "$TEST_DIR/envy_shim"
#!/bin/bash
exec /usr/bin/python3 -m envy.cli "$@"
EOF
chmod +x "$TEST_DIR/envy_shim"

export PYTHONPATH="__REPO_PATH__/src"
export ENVY_BIN="$TEST_DIR/envy_shim"

HOOK_CODE=$(/usr/bin/python3 -m envy.cli init bash)
eval "$HOOK_CODE"

OUT_FILE="$TEST_DIR/stdout.txt"
ERR_FILE="$TEST_DIR/stderr.txt"

ORIG_PATH="$PATH"
cd "$NO_VENV"
__envy_hook >/dev/null

# --- 1. Activation failure recovery & rollback ---
cd "$BROKEN_PROJ"
__envy_hook > "$OUT_FILE" 2> "$ERR_FILE"
out=$(cat "$OUT_FILE")
err=$(cat "$ERR_FILE")

if [[ "$out" == *"envy: venv loaded"* ]]; then
    echo "Should NOT report 'envy: venv loaded' when activation failed, got: $out" >&2
    exit 1
fi
if [[ "$err" != *"envy: failed to activate environment at"* ]]; then
    echo "Expected failure diagnostic on stderr, got: '$err'" >&2
    exit 1
fi
if [[ -n "$VIRTUAL_ENV" ]]; then
    echo "VIRTUAL_ENV was not rolled back after activation failure: $VIRTUAL_ENV" >&2
    exit 1
fi
if [[ -n "$ENVY_MANAGED_VENV" ]]; then
    echo "ENVY_MANAGED_VENV was set after activation failure: $ENVY_MANAGED_VENV" >&2
    exit 1
fi
if [[ "$PATH" != "$ORIG_PATH" ]]; then
    echo "PATH was not rolled back to original after activation failure!" >&2
    exit 1
fi
if type -t deactivate >/dev/null 2>&1; then
    echo "deactivate function was left behind after activation failure" >&2
    exit 1
fi

# Repeated prompt in broken_proj -> no repeated diagnostic or crash
__envy_hook > "$OUT_FILE" 2> "$ERR_FILE"
out_repeat=$(cat "$OUT_FILE")
err_repeat=$(cat "$ERR_FILE")
if [[ -n "$out_repeat" || -n "$err_repeat" ]]; then
    echo "Expected quiet repeat prompt in broken project, got out='$out_repeat', err='$err_repeat'" >&2
    exit 1
fi

# --- 2. Repair broken_proj and re-enter -> succeeds ---
create_valid_activate "$BROKEN_PROJ/.venv"

cd "$NO_VENV"
__envy_hook >/dev/null

cd "$BROKEN_PROJ"
__envy_hook > "$OUT_FILE" 2> "$ERR_FILE"
out=$(cat "$OUT_FILE")
if [[ "$out" != *"envy: venv loaded"* ]]; then
    echo "Repaired environment was not loaded on re-entry, got: $out" >&2
    exit 1
fi
if [[ "$VIRTUAL_ENV" != "$BROKEN_PROJ/.venv" ]]; then
    echo "Repaired environment VIRTUAL_ENV not set, got: $VIRTUAL_ENV" >&2
    exit 1
fi

# --- 3. Switch from valid environment to a broken environment ---
cd "$PROJ_A"
__envy_hook >/dev/null
if [[ "$VIRTUAL_ENV" != "$PROJ_A/.venv" ]]; then
    echo "projA not loaded, got: $VIRTUAL_ENV" >&2
    exit 1
fi

# Break broken_proj again
create_failing_activate "$BROKEN_PROJ/.venv"

cd "$BROKEN_PROJ"
__envy_hook > "$OUT_FILE" 2> "$ERR_FILE"
out=$(cat "$OUT_FILE")
err=$(cat "$ERR_FILE")

if [[ "$out" != *"envy: venv unloaded"* ]]; then
    echo "projA was not unloaded when switching to broken project, got: $out" >&2
    exit 1
fi
if [[ "$out" == *"envy: venv loaded"* ]]; then
    echo "broken project reported as loaded, got: $out" >&2
    exit 1
fi
if [[ "$err" != *"envy: failed to activate environment at"* ]]; then
    echo "Expected failure diagnostic on stderr for broken project, got: '$err'" >&2
    exit 1
fi
if [[ -n "$VIRTUAL_ENV" ]]; then
    echo "VIRTUAL_ENV not empty after switching to broken project: $VIRTUAL_ENV" >&2
    exit 1
fi
if [[ -n "$ENVY_MANAGED_VENV" ]]; then
    echo "ENVY_MANAGED_VENV not empty after switching to broken project: $ENVY_MANAGED_VENV" >&2
    exit 1
fi
if [[ "$PATH" != "$ORIG_PATH" ]]; then
    echo "PATH not restored after switching to broken project!" >&2
    exit 1
fi

# --- 4. Active .venv directory deleted while inside project ---
cd "$PROJ_A"
__envy_hook >/dev/null
if [[ "$VIRTUAL_ENV" != "$PROJ_A/.venv" ]]; then
    echo "projA failed to activate" >&2
    exit 1
fi

rm -rf "$PROJ_A/.venv"
__envy_hook > "$OUT_FILE" 2> "$ERR_FILE"
out=$(cat "$OUT_FILE")
if [[ "$out" != *"envy: venv unloaded"* ]]; then
    echo "Expected 'envy: venv unloaded' after deleting .venv, got: $out" >&2
    exit 1
fi
if [[ -n "$VIRTUAL_ENV" ]]; then
    echo "VIRTUAL_ENV was not cleared after .venv was deleted, got: $VIRTUAL_ENV" >&2
    exit 1
fi
if [[ -n "$ENVY_MANAGED_VENV" ]]; then
    echo "ENVY_MANAGED_VENV was not cleared after .venv was deleted, got: $ENVY_MANAGED_VENV" >&2
    exit 1
fi
if [[ "$PATH" != "$ORIG_PATH" ]]; then
    echo "PATH was not restored to original after deleting active .venv" >&2
    exit 1
fi

# --- 5. Active interpreter deleted while inside project ---
cd "$PROJ_B"
__envy_hook >/dev/null
if [[ "$VIRTUAL_ENV" != "$PROJ_B/.venv" ]]; then
    echo "projB failed to activate" >&2
    exit 1
fi

rm -f "$PROJ_B/.venv/bin/python"
__envy_hook > "$OUT_FILE" 2> "$ERR_FILE"
out=$(cat "$OUT_FILE")
err=$(cat "$ERR_FILE")

if [[ "$out" != *"envy: venv unloaded"* ]]; then
    echo "Expected 'envy: venv unloaded' after deleting interpreter, got: $out" >&2
    exit 1
fi
if [[ "$err" != *"envy: error:"* ]]; then
    echo "Expected resolver error diagnostic on stderr after deleting interpreter, got: '$err'" >&2
    exit 1
fi
if [[ -n "$VIRTUAL_ENV" ]]; then
    echo "VIRTUAL_ENV was not cleared after interpreter was deleted, got: $VIRTUAL_ENV" >&2
    exit 1
fi
if [[ -n "$ENVY_MANAGED_VENV" ]]; then
    echo "ENVY_MANAGED_VENV was not cleared after interpreter was deleted, got: $ENVY_MANAGED_VENV" >&2
    exit 1
fi

# --- 6. Active activation script deleted while inside project ---
ln -sf /usr/bin/python3 "$PROJ_B/.venv/bin/python"
cd "$NO_VENV"
__envy_hook >/dev/null
cd "$PROJ_B"
__envy_hook >/dev/null
if [[ "$VIRTUAL_ENV" != "$PROJ_B/.venv" ]]; then
    echo "projB failed to activate before deleting activate script" >&2
    exit 1
fi

rm -f "$PROJ_B/.venv/bin/activate"
__envy_hook > "$OUT_FILE" 2> "$ERR_FILE"
out=$(cat "$OUT_FILE")
err=$(cat "$ERR_FILE")

if [[ "$out" != *"envy: venv unloaded"* ]]; then
    echo "Expected 'envy: venv unloaded' after deleting activate script, got: $out" >&2
    exit 1
fi
if [[ "$err" != *"envy: error:"* ]]; then
    echo "Expected resolver error diagnostic on stderr after deleting activate script, got: '$err'" >&2
    exit 1
fi
if [[ -n "$VIRTUAL_ENV" ]]; then
    echo "VIRTUAL_ENV was not cleared after activate script was deleted, got: $VIRTUAL_ENV" >&2
    exit 1
fi
if [[ -n "$ENVY_MANAGED_VENV" ]]; then
    echo "ENVY_MANAGED_VENV was not cleared after activate script was deleted, got: $ENVY_MANAGED_VENV" >&2
    exit 1
fi

# --- 7. Function preservation: pre-existing deactivate function exact restoration ---
deactivate() { echo "CUSTOM_DEACTIVATE_ACTIVE"; }

# Case A: Failing activation preserves pre-existing deactivate function
cd "$NO_VENV"
__envy_hook >/dev/null
cd "$BROKEN_PROJ"
__envy_hook >/dev/null 2>&1
post_fail_deact=$(deactivate)
if [[ "$post_fail_deact" != "CUSTOM_DEACTIVATE_ACTIVE" ]]; then
    echo "Pre-existing deactivate function was lost after activation failure! Got: $post_fail_deact" >&2
    exit 1
fi

# Re-create valid activate script for projB
create_valid_activate "$PROJ_B/.venv"

# Case B: Successful activation and subsequent deactivation restores original deactivate function
cd "$NO_VENV"
__envy_hook >/dev/null
cd "$PROJ_B"
__envy_hook >/dev/null
if [[ "$VIRTUAL_ENV" != "$PROJ_B/.venv" ]]; then
    echo "projB failed to activate in function preservation test" >&2
    exit 1
fi
cd "$NO_VENV"
__envy_hook >/dev/null
post_unload_deact=$(deactivate)
if [[ "$post_unload_deact" != "CUSTOM_DEACTIVATE_ACTIVE" ]]; then
    echo "Pre-existing deactivate function was lost after managed venv unloaded! Got: $post_unload_deact" >&2
    exit 1
fi

# Case C: Explicit manual deactivation restores original deactivate function
cd "$PROJ_B"
__envy_hook >/dev/null
if [[ "$VIRTUAL_ENV" != "$PROJ_B/.venv" ]]; then
    echo "projB failed to activate for manual deactivation test" >&2
    exit 1
fi
deactivate
__envy_hook >/dev/null
post_manual_deact=$(deactivate)
if [[ "$post_manual_deact" != "CUSTOM_DEACTIVATE_ACTIVE" ]]; then
    echo "Pre-existing deactivate function was lost after manual deactivation! Got: $post_manual_deact" >&2
    exit 1
fi
# Sticky deactivation: should NOT reactivate on repeat in same directory
__envy_hook >/dev/null
if [[ -n "$VIRTUAL_ENV" ]]; then
    echo "Envy reactivated immediately after manual deactivation in same dir!" >&2
    exit 1
fi

# Case D: Re-entering project after manual deactivation re-activates, and subsequent unload preserves original function
cd "$NO_VENV"
__envy_hook >/dev/null
cd "$PROJ_B"
__envy_hook >/dev/null
if [[ "$VIRTUAL_ENV" != "$PROJ_B/.venv" ]]; then
    echo "projB failed to re-activate after re-entry!" >&2
    exit 1
fi
cd "$NO_VENV"
__envy_hook >/dev/null
post_reenter_deact=$(deactivate)
if [[ "$post_reenter_deact" != "CUSTOM_DEACTIVATE_ACTIVE" ]]; then
    echo "Pre-existing deactivate function was lost after re-entry activation/unload! Got: $post_reenter_deact" >&2
    exit 1
fi
unset -f deactivate

# --- 8. Bash rejecting a Windows-only environment health check ---
WIN_ONLY_DIR="$TEST_DIR/win_only_env"
mkdir -p "$WIN_ONLY_DIR/.venv/Scripts"
touch "$WIN_ONLY_DIR/.venv/Scripts/python.exe"
touch "$WIN_ONLY_DIR/.venv/Scripts/Activate.ps1"
cd "$WIN_ONLY_DIR"
__envy_hook >/dev/null 2>&1 || true
if [[ -n "$VIRTUAL_ENV" || -n "$ENVY_MANAGED_VENV" ]]; then
    echo "Bash hook accepted a Windows-only environment!" >&2
    exit 1
fi

# --- 8. Repeated activation/deactivation cycles: PATH stability ---
cd "$NO_VENV"
__envy_hook >/dev/null
PRE_LOOP_PATH="$PATH"

for i in {1..10}; do
    cd "$PROJ_B"
    __envy_hook >/dev/null
    cd "$NO_VENV"
    __envy_hook >/dev/null
done

if [[ "$PATH" != "$PRE_LOOP_PATH" ]]; then
    echo "PATH accumulated duplicate entries during 10 activation/deactivation cycles!" >&2
    echo "Initial: $PRE_LOOP_PATH" >&2
    echo "Final:   $PATH" >&2
    exit 1
fi

echo "ALL_BASH_LIFECYCLE_RELIABILITY_TESTS_PASSED"
""".replace("__REPO_PATH__", repo_wsl_path).replace("\r\n", "\n")

        res = subprocess.run([wsl, "bash"], input=script.encode("utf-8"), capture_output=True)
        self.assertEqual(res.returncode, 0, f"Failed: {res.stderr.decode('utf-8')}\nstdout: {res.stdout.decode('utf-8')}")
        self.assertIn("ALL_BASH_LIFECYCLE_RELIABILITY_TESTS_PASSED", res.stdout.decode("utf-8"))

    @unittest.skipUnless(is_wsl_available(), "WSL Bash is required for this test")
    def test_wsl_bash_subshell_initialization_and_isolation(self):
        """Verify nested interactive Bash initialization, environment isolation, and inherited state."""
        wsl = shutil.which("wsl.exe") or shutil.which("wsl") or "bash"
        repo_wsl_path = "/mnt/c/Users/fasiu/envy"

        script = r"""
set -e
TEST_DIR=$(mktemp -d /tmp/envy_bash_subshell_XXXXXX)
trap 'rm -rf "$TEST_DIR"' EXIT

PROJ_A="$TEST_DIR/projA"
PROJ_B="$TEST_DIR/projB"
NO_VENV="$TEST_DIR/no_venv"
mkdir -p "$PROJ_A/.venv/bin" "$PROJ_B/.venv/bin" "$NO_VENV"

create_activate() {
    local venv_dir="$1"
    local bin_dir="$venv_dir/bin"
    cat << 'EOF' > "$bin_dir/activate"
deactivate () {
    if [ -n "${_OLD_VIRTUAL_PATH:-}" ] ; then
        PATH="${_OLD_VIRTUAL_PATH}"
        export PATH
        unset _OLD_VIRTUAL_PATH
    fi
    unset VIRTUAL_ENV
    if [ ! "${1-}" = "nondestructive" ] ; then
        unset -f deactivate
    fi
}
deactivate nondestructive
VIRTUAL_ENV="__VENV_DIR__"
export VIRTUAL_ENV
_OLD_VIRTUAL_PATH="$PATH"
PATH="$bin_dir:$PATH"
export PATH
EOF
    sed -i "s|__VENV_DIR__|$venv_dir|g" "$bin_dir/activate"
    ln -sf /usr/bin/python3 "$bin_dir/python"
}

create_activate "$PROJ_A/.venv"
create_activate "$PROJ_B/.venv"

cat << 'EOF' > "$TEST_DIR/envy_shim"
#!/bin/bash
exec /usr/bin/python3 -m envy.cli "$@"
EOF
chmod +x "$TEST_DIR/envy_shim"

export PYTHONPATH="__REPO_PATH__/src"
export ENVY_BIN="$TEST_DIR/envy_shim"

CHILD_RC="$TEST_DIR/child_rc"
cat << 'EOF' > "$CHILD_RC"
export PYTHONPATH="__REPO_PATH__/src"
export ENVY_BIN="__ENVY_BIN__"
eval "$($ENVY_BIN init bash)"
EOF
sed -i "s|__REPO_PATH__|__REPO_PATH__|g" "$CHILD_RC"
sed -i "s|__ENVY_BIN__|$TEST_DIR/envy_shim|g" "$CHILD_RC"

# Generate hook for parent
HOOK_CODE=$(/usr/bin/python3 -m envy.cli init bash)
eval "$HOOK_CODE"

cd "$PROJ_A"
__envy_hook >/dev/null

if [[ "$VIRTUAL_ENV" != "$PROJ_A/.venv" ]]; then
    echo "Parent failed to activate projA: $VIRTUAL_ENV" >&2
    exit 1
fi
if [[ "$ENVY_MANAGED_VENV" != "$PROJ_A/.venv" ]]; then
    echo "Parent ENVY_MANAGED_VENV not set: $ENVY_MANAGED_VENV" >&2
    exit 1
fi

# Verify unexported bookkeeping variables are NOT in environment
if env | grep -q "^__ENVY_HOOKED="; then
    echo "__ENVY_HOOKED is exported in parent!" >&2
    exit 1
fi
if env | grep -q "^ENVY_MANAGED_VENV="; then
    echo "ENVY_MANAGED_VENV is exported in parent!" >&2
    exit 1
fi

# Spawn interactive child shell with explicit startup configuration
bash --noprofile --rcfile "$CHILD_RC" -i << EOF
if ! type -t __envy_hook >/dev/null 2>&1; then
    echo "Child shell failed to initialize envy hook!" >&2
    exit 1
fi
if [[ "\$VIRTUAL_ENV" != "$PROJ_A/.venv" ]]; then
    echo "Child did not inherit VIRTUAL_ENV from parent: \$VIRTUAL_ENV" >&2
    exit 1
fi
if [[ -n "\$ENVY_MANAGED_VENV" ]]; then
    echo "Child incorrectly inherited ENVY_MANAGED_VENV: \$ENVY_MANAGED_VENV" >&2
    exit 1
fi

# Inherited venv is treated as manual user control in child: navigating does not overwrite it
cd "$PROJ_B"
__envy_hook >/dev/null
if [[ "\$VIRTUAL_ENV" != "$PROJ_A/.venv" ]]; then
    echo "Child did not respect manual/inherited venv on navigation: \$VIRTUAL_ENV" >&2
    exit 1
fi

# Unsetting inherited VIRTUAL_ENV in child enables Envy auto-switching in child
unset VIRTUAL_ENV
__envy_hook >/dev/null

# Entering projB in child now auto-activates projB under child Envy management
cd "$NO_VENV"
__envy_hook >/dev/null
cd "$PROJ_B"
__envy_hook >/dev/null
if [[ "\$VIRTUAL_ENV" != "$PROJ_B/.venv" ]]; then
    echo "Child failed to activate projB: \$VIRTUAL_ENV" >&2
    exit 1
fi
if [[ "\$ENVY_MANAGED_VENV" != "$PROJ_B/.venv" ]]; then
    echo "Child failed to manage projB: \$ENVY_MANAGED_VENV" >&2
    exit 1
fi
exit 0
EOF

child_exit=$?
if [[ $child_exit -ne 0 ]]; then
    echo "Child shell exited with error: $child_exit" >&2
    exit 1
fi

# Verify parent shell state remains 100% intact and uncorrupted
if [[ "$VIRTUAL_ENV" != "$PROJ_A/.venv" ]]; then
    echo "Parent VIRTUAL_ENV was corrupted by child! Now: $VIRTUAL_ENV" >&2
    exit 1
fi
if [[ "$ENVY_MANAGED_VENV" != "$PROJ_A/.venv" ]]; then
    echo "Parent ENVY_MANAGED_VENV was corrupted by child! Now: $ENVY_MANAGED_VENV" >&2
    exit 1
fi

# Parent can continue normal operations
cd "$PROJ_B"
__envy_hook >/dev/null
if [[ "$VIRTUAL_ENV" != "$PROJ_B/.venv" ]]; then
    echo "Parent failed to switch to projB: $VIRTUAL_ENV" >&2
    exit 1
fi

cd "$NO_VENV"
__envy_hook >/dev/null
if [[ -n "$VIRTUAL_ENV" ]]; then
    echo "Parent failed to deactivate in no_venv: $VIRTUAL_ENV" >&2
    exit 1
fi

echo "ALL_BASH_SUBSHELL_TESTS_PASSED"
""".replace("__REPO_PATH__", repo_wsl_path).replace("\r\n", "\n")

        res = subprocess.run([wsl, "bash"], input=script.encode("utf-8"), capture_output=True)
        self.assertEqual(res.returncode, 0, f"Failed: {res.stderr.decode('utf-8')}\nstdout: {res.stdout.decode('utf-8')}")
        self.assertIn("ALL_BASH_SUBSHELL_TESTS_PASSED", res.stdout.decode("utf-8"))


if __name__ == "__main__":
    unittest.main()
