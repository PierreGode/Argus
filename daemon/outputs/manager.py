"""Run every enabled output concurrently, each on its own thread.

The manager is deliberately thin: outputs own their own connect/reconnect, so
all the manager does is start each enabled output, isolate crashes (one output
failing never tears down another), and wait for the daemon-wide stop.
"""

from __future__ import annotations

import threading

import tray_ui
from .base import Output, RunContext


class OutputManager:
    def __init__(self, outputs: list[Output], ctx: RunContext):
        self._outputs = outputs
        self._ctx = ctx

    def run(self) -> None:
        cfg = tray_ui.load_config()
        active = [o for o in self._outputs if o.enabled(cfg)]
        self._ctx.log(
            "Outputs: " + ", ".join(o.name for o in active) if active else "Outputs: none"
        )

        threads: list[threading.Thread] = []
        for o in active:
            t = threading.Thread(target=self._run_one, args=(o,),
                                 name=f"output-{o.name}", daemon=True)
            t.start()
            threads.append(t)

        # Block until quit, then let each output wind down (they honor stop).
        self._ctx.stop_event.wait()
        for t in threads:
            t.join(timeout=5)

    def _run_one(self, output: Output) -> None:
        try:
            output.run(self._ctx)
        except Exception as e:
            self._ctx.log(f"output '{output.name}' crashed: {e}")
