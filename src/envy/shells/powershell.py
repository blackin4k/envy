"""PowerShell integration hook generator for Envy."""

from pathlib import Path
import shutil
import sys

POWERSHELL_HOOK_TEMPLATE = r"""# Envy PowerShell Integration
# Automatically switches Python virtual environments on directory changes.

$global:__envy_hooked = (Get-Variable __envy_hooked -ErrorAction SilentlyContinue -ValueOnly)

# Preserve valid existing Envy ownership state if active and matching
$activeVenv = if ($env:VIRTUAL_ENV) { $env:VIRTUAL_ENV.TrimEnd('\/') } else { $null }
$managedVenv = if ($global:ENVY_MANAGED_VENV) { $global:ENVY_MANAGED_VENV.TrimEnd('\/') } else { $null }
$isManagedValid = ($managedVenv -and $activeVenv -and ($managedVenv -eq $activeVenv) -and (
    (Test-Path $managedVenv -PathType Container) -and
    (Test-Path (Join-Path $managedVenv "Scripts\python.exe")) -and
    (Test-Path (Join-Path $managedVenv "Scripts\Activate.ps1"))
))

if (-not $isManagedValid) {
    $global:ENVY_MANAGED_VENV = $null
    $global:__ENVY_PREV_PATH = $null
    $global:__ENVY_SAVED_DEACTIVATE = $null
}

$global:ENVY_DEACTIVATED_DIR = $null
$global:ENVY_FAILED_DIR = $null
$global:__envy_oldpwd = $null

# Preserve original prompt, never wrapping an existing envy prompt
$promptCmd = Get-Command prompt -ErrorAction SilentlyContinue
$isEnvyPrompt = ($null -ne $promptCmd -and $promptCmd.Definition -match '__envy_hook')
if ($null -eq $global:__envy_prompt_old -and -not $isEnvyPrompt) {
    $global:__envy_prompt_old = $function:prompt
}

$global:__envy_hooked = 1

function global:__envy_bin {
    $prevEA = $ErrorActionPreference
    try {
        $ErrorActionPreference = 'Continue'
        if (Get-Command envy -ErrorAction SilentlyContinue) {
            envy @args
        } elseif (Test-Path "__ENVY_BIN_PATH__") {
            & "__ENVY_BIN_PATH__" @args
        } else {
            Write-Error "envy binary not found in PATH or at __ENVY_BIN_PATH__"
        }
    } finally {
        $ErrorActionPreference = $prevEA
    }
}

function global:__envy_pwd {
    $cwd = Get-Location
    if ($cwd.Provider.Name -eq "FileSystem") {
        return $cwd.ProviderPath
    }
    return $null
}

function global:__envy_deactivate {
    param($Notifications = $null)
    if ($global:ENVY_MANAGED_VENV) {
        $deactivated = $false
        if (Get-Command deactivate -ErrorAction SilentlyContinue) {
            try {
                deactivate
                $deactivated = $true
            } catch {
                $deactivated = $false
            }
        }

        # Fallback cleanup in case deactivate was missing or failed to clean up
        if (Test-Path Env:_OLD_VIRTUAL_PATH) {
            Copy-Item -Path Env:_OLD_VIRTUAL_PATH -Destination Env:PATH
            Remove-Item -Path Env:_OLD_VIRTUAL_PATH -ErrorAction SilentlyContinue
        } elseif ($global:__ENVY_PREV_PATH) {
            $env:PATH = $global:__ENVY_PREV_PATH
        }

        if (Test-Path Env:_OLD_VIRTUAL_PYTHONHOME) {
            Copy-Item -Path Env:_OLD_VIRTUAL_PYTHONHOME -Destination Env:PYTHONHOME
            Remove-Item -Path Env:_OLD_VIRTUAL_PYTHONHOME -ErrorAction SilentlyContinue
        }

        Remove-Item env:VIRTUAL_ENV -ErrorAction SilentlyContinue
        Remove-Item env:VIRTUAL_ENV_PROMPT -ErrorAction SilentlyContinue
        Remove-Item function:_OLD_VIRTUAL_PROMPT -ErrorAction SilentlyContinue
        Remove-Variable _PYTHON_VENV_PROMPT_PREFIX -Scope Global -Force -ErrorAction SilentlyContinue
        $global:__ENVY_PREV_PATH = $null

        # Restore pre-existing deactivate function if there was one; otherwise ensure cleanup
        if ($null -ne $global:__ENVY_SAVED_DEACTIVATE) {
            ${function:global:deactivate} = $global:__ENVY_SAVED_DEACTIVATE
            $global:__ENVY_SAVED_DEACTIVATE = $null
        } else {
            Remove-Item function:deactivate -ErrorAction SilentlyContinue
        }

        $global:ENVY_MANAGED_VENV = $null
        if ($null -ne $Notifications) {
            $null = $Notifications.Add("envy: venv unloaded")
        }
    }
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
        if ($null -ne $global:__ENVY_SAVED_DEACTIVATE) {
            ${function:global:deactivate} = $global:__ENVY_SAVED_DEACTIVATE
            $global:__ENVY_SAVED_DEACTIVATE = $null
        }
    }

    # If directory has not changed and managed state matches environment, nothing to do
    # BUT if managed venv, its Windows interpreter, or its Windows activation script was deleted on disk, do not return early
    $venvValid = (-not $managedVenv -or (
        (Test-Path $managedVenv -PathType Container) -and
        (Test-Path (Join-Path $managedVenv "Scripts\python.exe")) -and
        (Test-Path (Join-Path $managedVenv "Scripts\Activate.ps1"))
    ))
    if ($dir -eq $global:__envy_oldpwd -and $managedVenv -eq $activeVenv -and $venvValid) {
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
    $rawOutput = (__envy_bin resolve "$dir")
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
            __envy_deactivate -Notifications $Notifications

            $activateScript = Join-Path $resolved "Scripts\Activate.ps1"
            if (-not (Test-Path $activateScript)) {
                $activateScript = Join-Path $resolved "bin\Activate.ps1"
            }

            if (Test-Path $activateScript) {
                # Snapshot pre-activation state for rollback on failure
                $prePath = $env:PATH
                $preVirtualEnv = $env:VIRTUAL_ENV
                $preVirtualEnvPrompt = $env:VIRTUAL_ENV_PROMPT
                $prePythonHome = $env:PYTHONHOME
                $preDeactivate = if (Get-Command deactivate -ErrorAction SilentlyContinue) {
                    ${function:deactivate}
                } else {
                    $null
                }

                $prevDisablePrompt = $env:VIRTUAL_ENV_DISABLE_PROMPT
                $env:VIRTUAL_ENV_DISABLE_PROMPT = 1
                $actOk = $false
                try {
                    . $activateScript
                    if ($env:VIRTUAL_ENV) {
                        $actOk = $true
                    }
                } catch {
                    $actOk = $false
                } finally {
                    if ($null -eq $prevDisablePrompt) {
                        Remove-Item env:VIRTUAL_ENV_DISABLE_PROMPT -ErrorAction SilentlyContinue
                    } else {
                        $env:VIRTUAL_ENV_DISABLE_PROMPT = $prevDisablePrompt
                    }
                }

                if ($actOk) {
                    $global:ENVY_MANAGED_VENV = if ($env:VIRTUAL_ENV) { $env:VIRTUAL_ENV.TrimEnd('\/') } else { $normResolved }
                    $global:__ENVY_PREV_PATH = $prePath
                    $global:__ENVY_SAVED_DEACTIVATE = $preDeactivate
                    $global:ENVY_FAILED_DIR = $null
                    if ($null -ne $Notifications) {
                        $null = $Notifications.Add("envy: venv loaded")
                    }
                } else {
                    # Rollback on activation failure
                    if (Get-Command deactivate -ErrorAction SilentlyContinue) {
                        try { deactivate } catch { }
                    }
                    $env:PATH = $prePath
                    if ($preVirtualEnv) {
                        $env:VIRTUAL_ENV = $preVirtualEnv
                    } else {
                        Remove-Item env:VIRTUAL_ENV -ErrorAction SilentlyContinue
                    }
                    if ($preVirtualEnvPrompt) {
                        $env:VIRTUAL_ENV_PROMPT = $preVirtualEnvPrompt
                    } else {
                        Remove-Item env:VIRTUAL_ENV_PROMPT -ErrorAction SilentlyContinue
                    }
                    if ($prePythonHome) {
                        $env:PYTHONHOME = $prePythonHome
                    } else {
                        Remove-Item env:PYTHONHOME -ErrorAction SilentlyContinue
                    }
                    Remove-Item env:_OLD_VIRTUAL_PATH -ErrorAction SilentlyContinue
                    Remove-Item env:_OLD_VIRTUAL_PYTHONHOME -ErrorAction SilentlyContinue
                    Remove-Item function:_OLD_VIRTUAL_PROMPT -ErrorAction SilentlyContinue
                    Remove-Variable _PYTHON_VENV_PROMPT_PREFIX -Scope Global -Force -ErrorAction SilentlyContinue
                    $global:__ENVY_PREV_PATH = $null

                    # Restore pre-existing deactivate function if there was one; otherwise ensure cleanup
                    if ($null -ne $preDeactivate) {
                        ${function:global:deactivate} = $preDeactivate
                    } else {
                        Remove-Item function:deactivate -ErrorAction SilentlyContinue
                    }
                    $global:__ENVY_SAVED_DEACTIVATE = $null

                    $global:ENVY_FAILED_DIR = $dir
                    $global:ENVY_MANAGED_VENV = $null
                    [Console]::Error.WriteLine("envy: failed to activate environment at '$resolved'")
                }
            }
        }
    } else {
        # No venv found for this directory; deactivate managed venv if one was active
        __envy_deactivate -Notifications $Notifications
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
