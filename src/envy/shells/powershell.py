"""PowerShell integration hook generator for Envy."""

from pathlib import Path
import shutil
import sys

POWERSHELL_HOOK_TEMPLATE = r"""# Envy PowerShell Integration
# Automatically switches Python virtual environments on directory changes.

$global:__envy_hooked = (Get-Variable __envy_hooked -ErrorAction SilentlyContinue -ValueOnly)
if ($global:__envy_hooked -ne 1) {
    $global:__envy_hooked = 1
    $global:ENVY_MANAGED_VENV = $null
    $global:ENVY_DEACTIVATED_DIR = $null
    $global:ENVY_FAILED_DIR = $null
    $global:__envy_oldpwd = $null
    $global:__envy_prompt_old = $function:prompt

    function global:__envy_bin {
        if (Get-Command envy -ErrorAction SilentlyContinue) {
            envy @args
        } elseif (Test-Path "__ENVY_BIN_PATH__") {
            & "__ENVY_BIN_PATH__" @args
        } else {
            Write-Error "envy binary not found in PATH or at __ENVY_BIN_PATH__"
        }
    }

    function global:__envy_pwd {
        $cwd = Get-Location
        if ($cwd.Provider.Name -eq "FileSystem") {
            return $cwd.ProviderPath
        }
        return $null
    }

    function global:__envy_hook {
        param($Notifications = $null)
        $dir = __envy_pwd
        if ($null -eq $dir) {
            return
        }

        $activeVenv = if ($env:VIRTUAL_ENV) { $env:VIRTUAL_ENV.TrimEnd('\/') } else { $null }
        $managedVenv = if ($global:ENVY_MANAGED_VENV) { $global:ENVY_MANAGED_VENV.TrimEnd('\/') } else { $null }

        # If user manually activated a venv, never touch or deactivate it
        if ($activeVenv -and ($activeVenv -ne $managedVenv)) {
            $global:ENVY_MANAGED_VENV = $null
            return
        }

        # If user explicitly called 'deactivate' inside a managed venv directory,
        # respect manual deactivation and do not reactivate until leaving this directory
        if ($managedVenv -and -not $activeVenv) {
            $global:ENVY_DEACTIVATED_DIR = $dir
            $global:ENVY_MANAGED_VENV = $null
        }

        # If directory has not changed and managed state matches environment, nothing to do
        if ($dir -eq $global:__envy_oldpwd -and $managedVenv -eq $activeVenv) {
            return
        }

        # Reset manual deactivation memory when moving to a different directory
        if ($null -ne $global:ENVY_DEACTIVATED_DIR -and $dir -ne $global:ENVY_DEACTIVATED_DIR) {
            $global:ENVY_DEACTIVATED_DIR = $null
        }

        # Reset failed directory memory when moving to a different directory
        if ($null -ne $global:ENVY_FAILED_DIR -and $dir -ne $global:ENVY_FAILED_DIR) {
            $global:ENVY_FAILED_DIR = $null
        }

        $global:__envy_oldpwd = $dir

        if ($null -ne $global:ENVY_DEACTIVATED_DIR -and $dir -eq $global:ENVY_DEACTIVATED_DIR) {
            return
        }

        if ($null -ne $global:ENVY_FAILED_DIR -and $dir -eq $global:ENVY_FAILED_DIR) {
            return
        }

        # Query envy for the environment containing or nearest to current directory
        $rawOutput = (__envy_bin resolve "$dir" 2>$null)
        $resolved = $null
        if ($rawOutput) {
            $lines = ($rawOutput | Out-String).Trim() -split "`r?`n"
            if ($lines.Length -gt 0) {
                $resolved = $lines[-1].Trim()
            }
        }

        if ($resolved -and (Test-Path $resolved -PathType Container)) {
            $normResolved = $resolved.TrimEnd('\/')
            if ($managedVenv -ne $normResolved) {
                # Deactivate previously managed venv if one was active
                if ($managedVenv -and (Get-Command deactivate -ErrorAction SilentlyContinue)) {
                    deactivate
                    $global:ENVY_MANAGED_VENV = $null
                    if ($null -ne $Notifications) {
                        $null = $Notifications.Add("envy: venv unloaded")
                    }
                }

                $activateScript = Join-Path $resolved "Scripts\Activate.ps1"
                if (-not (Test-Path $activateScript)) {
                    $activateScript = Join-Path $resolved "bin\Activate.ps1"
                }

                if (Test-Path $activateScript) {
                    $prevDisablePrompt = $env:VIRTUAL_ENV_DISABLE_PROMPT
                    $env:VIRTUAL_ENV_DISABLE_PROMPT = 1
                    $activated = $false
                    try {
                        . $activateScript
                        $activated = $true
                    } catch {
                        Write-Warning "Envy failed to activate environment at '$resolved': $_"
                    } finally {
                        if ($null -eq $prevDisablePrompt) {
                            Remove-Item env:VIRTUAL_ENV_DISABLE_PROMPT -ErrorAction SilentlyContinue
                        } else {
                            $env:VIRTUAL_ENV_DISABLE_PROMPT = $prevDisablePrompt
                        }
                    }

                    if ($activated) {
                        $global:ENVY_MANAGED_VENV = if ($env:VIRTUAL_ENV) { $env:VIRTUAL_ENV.TrimEnd('\/') } else { $normResolved }
                        $global:ENVY_FAILED_DIR = $null
                        if ($null -ne $Notifications) {
                            $null = $Notifications.Add("envy: venv loaded")
                        }
                    } else {
                        $global:ENVY_FAILED_DIR = $dir
                        $global:ENVY_MANAGED_VENV = $null
                    }
                }
            }
        } else {
            # No venv found for this directory; deactivate managed venv if one was active
            if ($managedVenv) {
                if (Get-Command deactivate -ErrorAction SilentlyContinue) {
                    deactivate
                }
                $global:ENVY_MANAGED_VENV = $null
                if ($null -ne $Notifications) {
                    $null = $Notifications.Add("envy: venv unloaded")
                }
            }
        }
    }

    function global:prompt {
        $origLastExitCode = $LASTEXITCODE
        $notifs = [System.Collections.Generic.List[string]]::new()
        try {
            __envy_hook -Notifications $notifs
        } catch {
            # Catch internal hook errors so prompt is never broken
        }

        # Restore original exit code before invoking chained prompt
        $global:LASTEXITCODE = $origLastExitCode
        $oldPrompt = if ($null -ne $global:__envy_prompt_old) {
            & $global:__envy_prompt_old
        } else {
            ""
        }

        $promptStr = if ($oldPrompt -is [System.Array]) {
            $oldPrompt -join "`n"
        } else {
            [string]$oldPrompt
        }

        if ($notifs.Count -gt 0) {
            $notifStr = ($notifs -join "`n")
            if ($promptStr) {
                return "$notifStr`n$promptStr"
            } else {
                return $notifStr
            }
        }

        return $promptStr
    }
}
"""


def get_powershell_hook(envy_bin: str | None = None) -> str:
    """Generate the PowerShell hook script."""
    if envy_bin is None:
        which_bin = shutil.which("envy")
        if which_bin:
            envy_bin = which_bin
        else:
            sibling = Path(sys.executable).parent / ("envy.exe" if sys.platform == "win32" else "envy")
            if sibling.is_file():
                envy_bin = str(sibling)
            else:
                argv_bin = Path(sys.argv[0]).resolve()
                if argv_bin.is_file():
                    envy_bin = str(argv_bin)
                else:
                    envy_bin = "envy"

    escaped_bin = envy_bin.replace("`", "``").replace("$", "`$").replace('"', '`"')
    return POWERSHELL_HOOK_TEMPLATE.replace("__ENVY_BIN_PATH__", escaped_bin)
