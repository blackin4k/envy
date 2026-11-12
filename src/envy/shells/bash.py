"""Bash integration hook generator for Envy."""

from pathlib import Path
import shutil
import sys

BASH_HOOK_TEMPLATE = r"""# Envy Bash Integration
# Automatically switches Python virtual environments on directory changes.

if [[ -z "${__ENVY_HOOKED:-}" ]]; then
    export __ENVY_HOOKED=1
    export ENVY_MANAGED_VENV=""
    export ENVY_DEACTIVATED_DIR=""
    export ENVY_FAILED_DIR=""
    export __ENVY_PREV_PATH=""
    export __ENVY_SAVED_DEACTIVATE_DEF=""
    __envy_oldpwd=""

    __envy_bin() {
        if \command -v envy >/dev/null 2>&1; then
            \command envy "$@"
        elif [[ -n "${ENVY_BIN:-}" && -x "${ENVY_BIN}" ]]; then
            "${ENVY_BIN}" "$@"
        elif [[ -x "__ENVY_BIN_PATH__" ]]; then
            "__ENVY_BIN_PATH__" "$@"
        else
            \builtin echo "envy binary not found in PATH or at __ENVY_BIN_PATH__" >&2
            return 1
        fi
    }

    __envy_deactivate() {
        if [[ -n "${ENVY_MANAGED_VENV:-}" ]]; then
            \builtin local deactivated=0
            if \builtin type -t deactivate >/dev/null 2>&1; then
                deactivate 2>/dev/null && deactivated=1 || deactivated=0
            fi

            # Fallback cleanup in case deactivate was missing or failed to clean up
            if [[ -n "${_OLD_VIRTUAL_PATH:-}" ]]; then
                export PATH="${_OLD_VIRTUAL_PATH}"
                \builtin unset _OLD_VIRTUAL_PATH
            elif [[ -n "${__ENVY_PREV_PATH:-}" ]]; then
                export PATH="${__ENVY_PREV_PATH}"
            fi

            if [[ -n "${_OLD_VIRTUAL_PYTHONHOME:-}" ]]; then
                export PYTHONHOME="${_OLD_VIRTUAL_PYTHONHOME}"
                \builtin unset _OLD_VIRTUAL_PYTHONHOME
            fi

            \builtin unset VIRTUAL_ENV
            \builtin unset VIRTUAL_ENV_PROMPT
            \builtin unset _OLD_VIRTUAL_PS1
            \builtin unset __ENVY_PREV_PATH

            # Restore pre-existing deactivate function if there was one; otherwise ensure cleanup
            if [[ -n "${__ENVY_SAVED_DEACTIVATE_DEF:-}" ]]; then
                \builtin eval "${__ENVY_SAVED_DEACTIVATE_DEF}" 2>/dev/null || true
                export __ENVY_SAVED_DEACTIVATE_DEF=""
            else
                \builtin unset -f deactivate 2>/dev/null || true
            fi
            \builtin hash -r 2>/dev/null || true

            export ENVY_MANAGED_VENV=""
            \builtin echo "envy: venv unloaded"
        fi
    }

    __envy_hook() {
        \builtin local -r retval="$?"
        \builtin local dir
        dir="$(\builtin pwd -P 2>/dev/null)" || return "${retval}"

        # If a virtual environment is active but not managed by Envy, respect manual user control
        if [[ -n "${VIRTUAL_ENV:-}" && "${VIRTUAL_ENV%/}" != "${ENVY_MANAGED_VENV%/}" ]]; then
            export ENVY_MANAGED_VENV=""
            return "${retval}"
        fi

        # If user explicitly called 'deactivate' inside a managed venv directory,
        # respect manual deactivation and do not reactivate until leaving this directory
        if [[ -n "${ENVY_MANAGED_VENV:-}" && -z "${VIRTUAL_ENV:-}" ]]; then
            export ENVY_DEACTIVATED_DIR="${dir}"
            export ENVY_MANAGED_VENV=""
            if [[ -n "${__ENVY_SAVED_DEACTIVATE_DEF:-}" ]]; then
                \builtin eval "${__ENVY_SAVED_DEACTIVATE_DEF}" 2>/dev/null || true
                export __ENVY_SAVED_DEACTIVATE_DEF=""
            fi
        fi

        # If directory has not changed and managed state matches environment, nothing to do
        # BUT if managed venv, its Linux interpreter, or its Linux activation script was deleted on disk, do not return early
        \builtin local venv_valid=1
        if [[ -n "${ENVY_MANAGED_VENV:-}" ]]; then
            if [[ ! -d "${ENVY_MANAGED_VENV}" || \
                  ! -e "${ENVY_MANAGED_VENV}/bin/python" || \
                  ! -f "${ENVY_MANAGED_VENV}/bin/activate" ]]; then
                venv_valid=0
            fi
        fi

        if [[ "${dir}" == "${__envy_oldpwd}" && "${ENVY_MANAGED_VENV%/}" == "${VIRTUAL_ENV%/}" && ${venv_valid} -eq 1 ]]; then
            return "${retval}"
        fi

        # Reset manual deactivation memory when moving to a different directory
        if [[ -n "${ENVY_DEACTIVATED_DIR:-}" && "${dir}" != "${ENVY_DEACTIVATED_DIR}" ]]; then
            export ENVY_DEACTIVATED_DIR=""
        fi

        # Reset failed directory memory when moving to a different directory
        if [[ -n "${ENVY_FAILED_DIR:-}" && "${dir}" != "${ENVY_FAILED_DIR}" ]]; then
            export ENVY_FAILED_DIR=""
        fi

        __envy_oldpwd="${dir}"

        if [[ -n "${ENVY_DEACTIVATED_DIR:-}" && "${dir}" == "${ENVY_DEACTIVATED_DIR}" ]]; then
            return "${retval}"
        fi

        if [[ -n "${ENVY_FAILED_DIR:-}" && "${dir}" == "${ENVY_FAILED_DIR}" ]]; then
            return "${retval}"
        fi

        # Query envy for the environment containing or nearest to current directory
        \builtin local resolved
        resolved="$(__envy_bin resolve "${dir}" --shell bash)" || resolved=""
        resolved="${resolved%%$'\r'}"

        if [[ -n "${resolved}" && -d "${resolved}" ]]; then
            \builtin local norm_resolved="${resolved%/}"
            if [[ "${ENVY_MANAGED_VENV%/}" != "${norm_resolved}" ]]; then
                # Deactivate previously managed venv if one was active
                __envy_deactivate

                \builtin local activate_script="${resolved}/bin/activate"

                if [[ -f "${activate_script}" ]]; then
                    # Snapshot pre-activation shell state for rollback on failure
                    \builtin local _pre_path="${PATH}"
                    \builtin local _pre_venv="${VIRTUAL_ENV:-}"
                    \builtin local _pre_prompt="${VIRTUAL_ENV_PROMPT:-}"
                    \builtin local _pre_pyhome="${PYTHONHOME:-}"
                    \builtin local _pre_deactivate_def=""
                    if \builtin type -t deactivate >/dev/null 2>&1; then
                        _pre_deactivate_def="$(\builtin declare -f deactivate 2>/dev/null)"
                    fi

                    \builtin local prev_disable_prompt="${VIRTUAL_ENV_DISABLE_PROMPT:-}"
                    export VIRTUAL_ENV_DISABLE_PROMPT=1

                    \builtin local act_ok=0
                    if . "${activate_script}"; then
                        if [[ -n "${VIRTUAL_ENV:-}" ]]; then
                            act_ok=1
                        fi
                    fi

                    if [[ -z "${prev_disable_prompt}" ]]; then
                        \builtin unset VIRTUAL_ENV_DISABLE_PROMPT
                    else
                        export VIRTUAL_ENV_DISABLE_PROMPT="${prev_disable_prompt}"
                    fi

                    if [[ ${act_ok} -eq 1 ]]; then
                        export ENVY_MANAGED_VENV="${VIRTUAL_ENV:-${resolved}}"
                        export __ENVY_PREV_PATH="${_pre_path}"
                        export __ENVY_SAVED_DEACTIVATE_DEF="${_pre_deactivate_def}"
                        export ENVY_FAILED_DIR=""
                        \builtin echo "envy: venv loaded"
                    else
                        # Rollback on activation failure
                        if \builtin type -t deactivate >/dev/null 2>&1; then
                            deactivate 2>/dev/null || true
                        fi
                        export PATH="${_pre_path}"
                        if [[ -n "${_pre_venv}" ]]; then
                            export VIRTUAL_ENV="${_pre_venv}"
                        else
                            \builtin unset VIRTUAL_ENV
                        fi
                        if [[ -n "${_pre_prompt}" ]]; then
                            export VIRTUAL_ENV_PROMPT="${_pre_prompt}"
                        else
                            \builtin unset VIRTUAL_ENV_PROMPT
                        fi
                        if [[ -n "${_pre_pyhome}" ]]; then
                            export PYTHONHOME="${_pre_pyhome}"
                        else
                            \builtin unset PYTHONHOME
                        fi
                        \builtin unset _OLD_VIRTUAL_PATH
                        \builtin unset _OLD_VIRTUAL_PYTHONHOME
                        \builtin unset _OLD_VIRTUAL_PS1
                        \builtin unset __ENVY_PREV_PATH

                        # Restore pre-existing deactivate function if there was one; otherwise ensure cleanup
                        if [[ -n "${_pre_deactivate_def}" ]]; then
                            \builtin eval "${_pre_deactivate_def}" 2>/dev/null || true
                        else
                            \builtin unset -f deactivate 2>/dev/null || true
                        fi
                        export __ENVY_SAVED_DEACTIVATE_DEF=""
                        \builtin hash -r 2>/dev/null || true

                        export ENVY_FAILED_DIR="${dir}"
                        export ENVY_MANAGED_VENV=""
                        \builtin echo "envy: failed to activate environment at '${resolved}'" >&2
                    fi
                fi
            fi
        else
            # No venv found for this directory; deactivate managed venv if one was active
            __envy_deactivate
        fi

        return "${retval}"
    }

    # Register into PROMPT_COMMAND preserving both array and string forms
    if declare -p PROMPT_COMMAND 2>/dev/null | grep -q 'declare -a'; then
        _envy_already_hooked=0
        for item in "${PROMPT_COMMAND[@]}"; do
            if [[ "$item" == "__envy_hook" ]]; then
                _envy_already_hooked=1
                break
            fi
        done
        if [[ $_envy_already_hooked -eq 0 ]]; then
            PROMPT_COMMAND=("__envy_hook" "${PROMPT_COMMAND[@]}")
        fi
        unset _envy_already_hooked
    else
        if [[ ";${PROMPT_COMMAND:-};" != *";__envy_hook;"* ]]; then
            if [[ -z "${PROMPT_COMMAND:-}" ]]; then
                PROMPT_COMMAND="__envy_hook"
            else
                PROMPT_COMMAND="__envy_hook;${PROMPT_COMMAND}"
            fi
        fi
    fi
fi
"""


def get_bash_hook(envy_bin: str | None = None) -> str:
    """Generate the Bash hook script."""
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

    escaped_bin = envy_bin.replace("\\", "\\\\").replace('"', '\\"').replace("$", "\\$")
    return BASH_HOOK_TEMPLATE.replace("__ENVY_BIN_PATH__", escaped_bin)
