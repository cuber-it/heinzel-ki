"""MattermostAddOn — Mattermost als Eingangs- und Ausgangskanal für Heinzel.

Lauscht via WebSocket auf eingehende Messages, leitet sie an runner.chat()
weiter und postet die Antwort als Thread-Reply zurück.

Konfiguration (heinzel.yaml):
    addons:
      mattermost:
        url: http://services:8065
        token: xxx
        channel: heinzel-general
        team: ""                  # optional, hilft bei Channel-Auflösung
        mention_only: true        # nur auf @heinzel-name reagieren
        reply_in_thread: true     # Antwort als Thread-Reply (empfohlen)

Importpfad:
    from addons.mattermost import MattermostAddOn

Abhängigkeiten: keine anderen AddOns, braucht runner-Referenz in on_attach.
"""

from __future__ import annotations

import asyncio
import logging

from core.addon import AddOn

from .client import MattermostClient
from .models import MattermostMessage, MattermostReply

logger = logging.getLogger(__name__)


class MattermostAddOn(AddOn):
    """Mattermost-Kanal für Heinzel.

    Lifecycle:
        on_attach  → Channel-ID auflösen, eigene User-ID holen, WS-Loop starten
        on_detach  → WS-Loop stoppen, Connection schließen

    Message-Flow:
        WS Event → _handle_message() → mention/channel Filter
                 → runner.chat(text) → _post_reply(channel, text, root_id)
    """

    name = "mattermost"
    version = "0.1.0"
    dependencies: list[str] = []

    def __init__(
        self,
        url: str,
        token: str,
        channel: str,
        team: str = "",
        mention_only: bool = True,
        reply_in_thread: bool = True,
    ) -> None:
        self._client = MattermostClient(url=url, token=token)
        self._channel_name = channel
        self._team = team
        self._mention_only = mention_only
        self._reply_in_thread = reply_in_thread

        self._channel_id: str = ""
        self._my_id: str = ""
        self._my_username: str = ""
        self._runner = None          # gesetzt in on_attach
        self._ws_task: asyncio.Task | None = None
        self._ws_conn = None

    # -------------------------------------------------------------------------
    # AddOn Lifecycle
    # -------------------------------------------------------------------------

    async def on_attach(self, heinzel) -> None:
        """Channel-ID auflösen, eigene ID holen, WebSocket-Loop starten."""
        # Runner-Referenz holen
        try:
            self._runner = heinzel.runner
        except AttributeError:
            logger.warning("[MattermostAddOn] heinzel.runner nicht gefunden — kein Chat möglich")

        try:
            self._my_id = await self._client.get_my_id()
            self._my_username = await self._client.get_my_username()
            self._channel_id = await self._client.resolve_channel_id(
                self._channel_name, self._team
            )
        except Exception as exc:
            logger.error(f"[MattermostAddOn] Initialisierung fehlgeschlagen: {exc}")
            return

        # WebSocket-Loop als Background-Task
        self._ws_task = asyncio.create_task(
            self._ws_loop(),
            name="mattermost-ws-loop",
        )
        logger.info(
            f"[MattermostAddOn] bereit — Channel: '{self._channel_name}' "
            f"({self._channel_id}), mention_only={self._mention_only}, "
            f"user='{self._my_username}'"
        )

    async def on_detach(self, heinzel) -> None:
        """WebSocket-Loop stoppen."""
        if self._ws_task and not self._ws_task.done():
            self._ws_task.cancel()
            try:
                await self._ws_task
            except asyncio.CancelledError:
                pass
        if self._ws_conn:
            try:
                await self._ws_conn.close()
            except Exception:
                pass
        self._ws_task = None
        self._ws_conn = None
        self._runner = None
        logger.info("[MattermostAddOn] gestoppt")

    # -------------------------------------------------------------------------
    # Öffentliche API
    # -------------------------------------------------------------------------

    async def post(self, text: str, root_id: str = "") -> None:
        """Message direkt in den konfigurierten Channel posten."""
        await self._client.post_message(self._channel_id, text, root_id=root_id)

    async def post_to(self, channel_id: str, text: str, root_id: str = "") -> None:
        """Message in beliebigen Channel posten (für Agent-to-Agent)."""
        await self._client.post_message(channel_id, text, root_id=root_id)

    def is_connected(self) -> bool:
        return self._ws_task is not None and not self._ws_task.done()

    # -------------------------------------------------------------------------
    # WebSocket-Loop (Background-Task)
    # -------------------------------------------------------------------------

    async def _ws_loop(self) -> None:
        """Läuft als asyncio.Task — verbindet, liest Events, reconnectet bei Fehler."""
        retry_delay = 5

        while True:
            try:
                self._ws_conn = await self._client.connect_websocket()
                retry_delay = 5  # Reset bei erfolgreicher Verbindung

                async for event in self._client.iter_messages(self._ws_conn):
                    post = MattermostClient.parse_posted_event(event)
                    if post:
                        await self._handle_post(post)

            except asyncio.CancelledError:
                logger.info("[MattermostAddOn] WS-Loop abgebrochen")
                return
            except Exception as exc:
                logger.error(
                    f"[MattermostAddOn] WS-Fehler: {exc} — "
                    f"reconnect in {retry_delay}s"
                )
                await asyncio.sleep(retry_delay)
                retry_delay = min(retry_delay * 2, 60)  # Exponential backoff, max 60s

    # -------------------------------------------------------------------------
    # Message-Handling
    # -------------------------------------------------------------------------

    async def _handle_post(self, post: dict) -> None:
        """Eingehenden Post verarbeiten."""
        # Eigene Messages ignorieren
        if post.get("user_id") == self._my_id:
            return

        # Nur konfigurierten Channel
        if post.get("channel_id") != self._channel_id:
            return

        text: str = post.get("message", "").strip()
        if not text:
            return

        # Mention-Filter
        if self._mention_only and not self._is_mentioned(text):
            return

        msg = MattermostMessage(
            message_id=post.get("id", ""),
            channel_id=post.get("channel_id", ""),
            user_id=post.get("user_id", ""),
            username=post.get("props", {}).get("from_bot", "") or "",
            text=text,
            root_id=post.get("root_id", "") or post.get("id", ""),
            mentions=self._extract_mentions(text),
        )

        logger.info(
            f"[MattermostAddOn] Message von '{msg.user_id}': "
            f"{text[:60]}{'...' if len(text) > 60 else ''}"
        )

        await self._dispatch(msg)

    async def _dispatch(self, msg: MattermostMessage) -> None:
        """Message an Runner weiterleiten und Antwort posten."""
        if self._runner is None:
            logger.warning("[MattermostAddOn] kein Runner — Message ignoriert")
            return

        # @mention aus Text entfernen bevor ans LLM
        clean_text = self._strip_mention(msg.text)

        try:
            response = await self._runner.chat(clean_text)
        except Exception as exc:
            logger.error(f"[MattermostAddOn] runner.chat() Fehler: {exc}")
            response = f"Fehler bei der Verarbeitung: {exc}"

        if not response:
            return

        root_id = msg.root_id if self._reply_in_thread else ""
        await self._post_reply(MattermostReply(
            channel_id=msg.channel_id,
            text=response,
            root_id=root_id,
        ))

    async def _post_reply(self, reply: MattermostReply) -> None:
        """Antwort in Mattermost posten."""
        try:
            await self._client.post_message(
                reply.channel_id, reply.text, root_id=reply.root_id
            )
        except Exception as exc:
            logger.error(f"[MattermostAddOn] Fehler beim Posten: {exc}")

    # -------------------------------------------------------------------------
    # Hilfsfunktionen
    # -------------------------------------------------------------------------

    def _is_mentioned(self, text: str) -> bool:
        """Prüft ob @my_username im Text vorkommt."""
        return f"@{self._my_username}" in text

    def _extract_mentions(self, text: str) -> list[str]:
        """Alle @mentions aus Text extrahieren."""
        import re
        return re.findall(r"@(\w[\w.-]*)", text)

    def _strip_mention(self, text: str) -> str:
        """@my_username aus Text entfernen."""
        return text.replace(f"@{self._my_username}", "").strip()
