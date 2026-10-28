"""Conversation-owned runtime state; shared desktop access has one task owner."""
from dataclasses import dataclass, field
import threading

from .security import StopToken


@dataclass
class Session:
    cid: str
    browser: object
    agent: object = None
    worker: object = None
    start_token: StopToken = field(default_factory=StopToken)
    active_model: object = None
    pending_images: list = field(default_factory=list)
    pending_files: list = field(default_factory=list)
    stop_source: str | None = None
    saved_followup: bool = False
    last_reply: str = ''
    stream: str = ''
    preview_path: str = ''
    status: str = ''
    attention: bool = False
    done: dict | None = None
    generation: int = 0

    @property
    def running(self):
        return self.done is None and bool(self.worker and self.worker.is_alive())


def session_property(name):
    """UI compatibility: these fields always belong to the selected conversation."""
    return property(lambda app: getattr(app.current_session, name),
                    lambda app, value: setattr(app.current_session, name, value))


class DesktopLease:
    """Keep observation and subsequent input together until that task finishes.

    Model, file, network and isolated browser work can proceed independently.
    Waiting for the single physical desktop remains cancellable.
    """
    def __init__(self):
        self.condition = threading.Condition()
        self.owner = None

    def acquire(self, token, progress):
        with self.condition:
            if self.owner is not None and self.owner is not token:
                progress('Waiting for another conversation to finish using the computer…')
            while self.owner is not None and self.owner is not token:
                token.check()
                self.condition.wait(.05)
            token.check()
            self.owner = token

    def release(self, token):
        with self.condition:
            if self.owner is token:
                self.owner = None
                self.condition.notify_all()
