"""Pluggable daemon outputs.

An *output* is a device the daemon drives. Each runs in its own thread, owns its
own connection/reconnect, and reacts to a shared stop so any combination can run
at once (Argus only, lamp only, both, neither).

  - ArgusOutput    — the existing Argus device (BLE or USB), reused unchanged.
  - KlarLampOutput — the LilyGO T-Dongle "Klar-lampan" over USB CDC.

Add a future output by subclassing `Output` and registering it in the daemon's
`OutputManager` — no existing output changes.
"""

from .base import Output, RunContext
from .argus import ArgusOutput
from .klar_lamp import KlarLampOutput
from .manager import OutputManager

__all__ = ["Output", "RunContext", "ArgusOutput", "KlarLampOutput", "OutputManager"]
