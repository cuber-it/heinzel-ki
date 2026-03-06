"""Datenmodelle für MattermostAddOn."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class MattermostMessage:
    """Eingehende Mattermost-Nachricht (normalisiert)."""

    message_id: str
    channel_id: str
    user_id: str
    username: str
    text: str
    root_id: str = ""        # leer = neue Message, gesetzt = Thread-Reply
    mentions: list[str] = field(default_factory=list)

    @property
    def is_thread_reply(self) -> bool:
        return bool(self.root_id)


@dataclass
class MattermostReply:
    """Ausgehende Antwort an Mattermost."""

    channel_id: str
    text: str
    root_id: str = ""        # leer = neue Message, gesetzt = Thread-Reply
