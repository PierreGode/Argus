"""The Argus device as an output.

This is a thin adapter: it calls the daemon's existing transport runner
(`run_worker`, which picks BLE or USB from config and drives `run_ble` /
`run_serial`) completely unchanged. Wiring Argus through the Output interface
does not alter a single byte of what Argus sends or how it reconnects — it only
lets Argus run alongside other outputs.

The runner is injected (not imported) because it lives in `argus-daemon.py`,
whose hyphenated filename isn't importable as a module.
"""

from __future__ import annotations

from typing import Callable

from .base import Output, RunContext


class ArgusOutput(Output):
    name = "argus"

    def __init__(self, transport_runner: Callable):
        # transport_runner(args, token, stop_event, wake_event, reload_event)
        # == the daemon's run_worker.
        self._runner = transport_runner

    def run(self, ctx: RunContext) -> None:
        self._runner(ctx.args, ctx.token, ctx.stop_event,
                     ctx.wake_event, ctx.reload_event)
