"""Shell hook integrations for envy."""

from envy.shells.bash import get_bash_hook
from envy.shells.powershell import get_powershell_hook

__all__ = ["get_powershell_hook", "get_bash_hook"]
