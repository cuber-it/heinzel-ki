"""Mattermost REST + WebSocket Client.

Minimale Implementierung ohne externe Mattermost-Lib.
Nutzt httpx für REST und websockets für den Event-Stream.

Nur was der MattermostAddOn braucht:
  - Eigene User-ID holen (für Mention-Filter)
  - Channel-ID aus Name auflösen
  - Message posten (mit optionalem root_id für Thread-Reply)
  - WebSocket Event-Stream lesen
"""

from __future__ import annotations

import json
import logging
import urllib.parse

import httpx

logger = logging.getLogger(__name__)

# WebSocket Event-Typen die uns interessieren
_RELEVANT_EVENTS = {"posted"}


class MattermostClient:
    """Thin Client für Mattermost REST API v4 + WebSocket."""

    def __init__(self, url: str, token: str, timeout: int = 10) -> None:
        self._base = url.rstrip("/") + "/api/v4"
        self._ws_base = url.rstrip("/").replace("http://", "ws://").replace("https://", "wss://")
        self._token = token
        self._timeout = timeout
        self._headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        }
        self._me: dict | None = None   # Cache für eigenes User-Objekt

    # -------------------------------------------------------------------------
    # REST
    # -------------------------------------------------------------------------

    async def get_me(self) -> dict:
        """Eigenes User-Objekt holen (gecacht)."""
        if self._me is None:
            async with httpx.AsyncClient(headers=self._headers, timeout=self._timeout) as c:
                resp = await c.get(f"{self._base}/users/me")
                resp.raise_for_status()
                self._me = resp.json()
        return self._me

    async def get_my_id(self) -> str:
        me = await self.get_me()
        return me["id"]

    async def get_my_username(self) -> str:
        me = await self.get_me()
        return me["username"]

    async def resolve_channel_id(self, channel_name: str, team_name: str = "") -> str:
        """Channel-Name → Channel-ID.

        Wenn team_name angegeben: über Team-Route.
        Sonst: direkte Suche über /channels/search.
        """
        async with httpx.AsyncClient(headers=self._headers, timeout=self._timeout) as c:
            if team_name:
                resp = await c.get(
                    f"{self._base}/teams/name/{team_name}/channels/name/{channel_name}"
                )
            else:
                resp = await c.post(
                    f"{self._base}/channels/search",
                    json={"term": channel_name},
                )
                resp.raise_for_status()
                channels = resp.json()
                for ch in channels:
                    if ch.get("name") == channel_name:
                        return ch["id"]
                raise ValueError(f"Channel '{channel_name}' nicht gefunden")
            resp.raise_for_status()
            return resp.json()["id"]

    async def post_message(self, channel_id: str, text: str, root_id: str = "") -> dict:
        """Message in Channel posten. root_id für Thread-Reply."""
        payload: dict = {"channel_id": channel_id, "message": text}
        if root_id:
            payload["root_id"] = root_id

        async with httpx.AsyncClient(headers=self._headers, timeout=self._timeout) as c:
            resp = await c.post(f"{self._base}/posts", json=payload)
            resp.raise_for_status()
            return resp.json()

    # -------------------------------------------------------------------------
    # WebSocket
    # -------------------------------------------------------------------------

    async def connect_websocket(self):
        """WebSocket-Verbindung aufbauen und authentifizieren.

        Gibt eine offene websockets-Connection zurück.
        Caller ist für Cleanup (async with oder close()) verantwortlich.
        """
        import websockets

        ws_url = f"{self._ws_base}/api/v4/websocket"
        conn = await websockets.connect(ws_url)

        # Auth-Handshake
        auth = json.dumps({
            "seq": 1,
            "action": "authentication_challenge",
            "data": {"token": self._token},
        })
        await conn.send(auth)
        resp = json.loads(await conn.recv())
        if resp.get("status") != "OK":
            await conn.close()
            raise ConnectionError(f"Mattermost WebSocket Auth fehlgeschlagen: {resp}")

        logger.info("[MattermostClient] WebSocket verbunden und authentifiziert")
        return conn

    async def iter_messages(self, conn) -> "AsyncGenerator[MattermostRawEvent, None]":
        """Async-Generator: liefert geparste Events aus dem WebSocket-Stream."""
        import websockets

        async for raw in conn:
            try:
                event = json.loads(raw)
            except json.JSONDecodeError:
                continue

            event_type = event.get("event", "")
            if event_type not in _RELEVANT_EVENTS:
                continue

            yield event

    @staticmethod
    def parse_posted_event(event: dict) -> dict | None:
        """'posted'-Event → Post-Dict. None bei Parse-Fehler."""
        try:
            data = event.get("data", {})
            post = json.loads(data.get("post", "{}"))
            return post
        except (json.JSONDecodeError, AttributeError):
            return None
