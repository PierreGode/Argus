"""Output interface + the shared run context passed to every output."""

from __future__ import annotations

import threading
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Callable

from session_state import SessionStore


@dataclass
class RunContext:
    """Everything an output needs to do its job, shared across all outputs.

    `args`/`token` mirror the daemon's CLI + Anthropic token. The three events
    are the daemon-wide control signals the existing transport loops already
    honor: `stop_event` (quit), `wake_event` (push now), `reload_event`
    (transport setting changed). `store` is the live per-session state the lamp
    renders. `log` writes to the daemon log + tray view."""
    args: object
    token: object
    stop_event: threading.Event
    wake_event: threading.Event
    reload_event: threading.Event
    store: SessionStore
    log: Callable[[str], None]


class Output(ABC):
    """A device the daemon drives. One instance == one output, run on its own
    thread by the OutputManager."""

    name: str = "output"

    def enabled(self, cfg: dict) -> bool:
        """Whether this output should run given the current config."""
        return True

    @abstractmethod
    def run(self, ctx: RunContext) -> None:
        """Blocking run loop. Must return promptly once `ctx.stop_event` is set,
        and should handle its own connect/reconnect so an unplugged device never
        tears down other outputs."""
        raise NotImplementedError
