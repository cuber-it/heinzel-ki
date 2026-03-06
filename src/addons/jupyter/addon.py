"""JupyterAddOn — Code-Ausführung in laufendem Jupyter-Kernel.

Heinzel kann Python-Code ausführen und Output zurückbekommen.
Nutzt Jupyter Server REST API v2 + WebSocket für Kernel-Kommunikation.

Registriert als lokales Tool beim MCPToolsRouter:
    local:jupyter:execute_code(code) → ExecutionResult

Use Cases:
    - Data Analysis: Pandas-Code schreiben + ausführen + interpretieren
    - Iteratives Debugging: Code → Fehler → korrigieren
    - Visualisierungen: Plot-Output als Text-Beschreibung

Konfiguration (heinzel.yaml):
    addons:
      jupyter:
        url: http://services:8000
        token: "${JUPYTER_TOKEN}"
        kernel: python3
        timeout: 30

Sicherheit: Heinzel führt echten Code aus.
Kernel-Isolation liegt in der JupyterHub-Konfiguration.
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from dataclasses import dataclass, field
from typing import Any

import httpx

from core.addon import AddOn
from core.models import AddOnResult, PipelineContext, ContextHistory

logger = logging.getLogger(__name__)


# =============================================================================
# Datenmodelle
# =============================================================================


@dataclass
class ExecutionResult:
    """Ergebnis einer Code-Ausführung."""

    stdout: str = ""
    stderr: str = ""
    outputs: list[dict] = field(default_factory=list)   # Rich outputs (Bilder, DataFrames)
    error: str | None = None
    execution_count: int = 0

    @property
    def success(self) -> bool:
        return self.error is None

    def as_text(self) -> str:
        """Kompakte Textdarstellung für LLM."""
        parts = []
        if self.stdout:
            parts.append(f"stdout:\n{self.stdout.strip()}")
        if self.stderr:
            parts.append(f"stderr:\n{self.stderr.strip()}")
        if self.error:
            parts.append(f"error: {self.error}")
        for out in self.outputs:
            if out.get("type") == "text":
                parts.append(out.get("content", ""))
            elif out.get("type") == "image":
                parts.append(f"[Bild: {out.get('format', 'png')}]")
        return "\n".join(parts) if parts else "(kein Output)"


# =============================================================================
# JupyterClient — REST API
# =============================================================================


class JupyterClient:
    """Kommuniziert mit Jupyter Server REST API.

    Kein websockets-Dependency — polling-basierter Output-Abruf.
    Für Produktionsnutzung: WebSocket-basiertes execute empfohlen.
    """

    def __init__(self, url: str, token: str, timeout: int = 30) -> None:
        self._base = url.rstrip("/")
        self._token = token
        self._timeout = timeout
        self._headers = {
            "Authorization": f"token {token}",
            "Content-Type": "application/json",
        }
        self._client: httpx.AsyncClient | None = None
        self._kernel_id: str | None = None

    async def start(self, kernel_name: str = "python3") -> str:
        """Kernel starten, ID zurückgeben."""
        self._client = httpx.AsyncClient(
            headers=self._headers,
            timeout=self._timeout,
        )
        resp = await self._client.post(
            f"{self._base}/api/kernels",
            json={"name": kernel_name},
        )
        resp.raise_for_status()
        data = resp.json()
        self._kernel_id = data["id"]
        logger.info(f"[JupyterClient] Kernel gestartet: {self._kernel_id} ({kernel_name})")
        return self._kernel_id

    async def stop(self) -> None:
        """Kernel stoppen."""
        if self._client and self._kernel_id:
            try:
                await self._client.delete(
                    f"{self._base}/api/kernels/{self._kernel_id}"
                )
            except Exception as exc:
                logger.warning(f"[JupyterClient] Kernel-Stop Fehler: {exc}")
        if self._client:
            await self._client.aclose()
            self._client = None
        self._kernel_id = None

    async def execute(self, code: str) -> ExecutionResult:
        """Code im Kernel ausführen — wartet auf Ergebnis."""
        if not self._client or not self._kernel_id:
            return ExecutionResult(error="Kein aktiver Kernel")

        msg_id = str(uuid.uuid4())
        payload = {
            "code": code,
            "silent": False,
            "store_history": True,
            "msg_id": msg_id,
        }

        try:
            resp = await self._client.post(
                f"{self._base}/api/kernels/{self._kernel_id}/execute",
                json=payload,
            )
            resp.raise_for_status()
            data = resp.json()
            return _parse_execute_response(data)
        except httpx.HTTPStatusError as exc:
            return ExecutionResult(error=f"HTTP {exc.response.status_code}: {exc.response.text}")
        except Exception as exc:
            return ExecutionResult(error=str(exc))

    async def list_kernels(self) -> list[dict]:
        """Alle laufenden Kernels."""
        if not self._client:
            return []
        resp = await self._client.get(f"{self._base}/api/kernels")
        resp.raise_for_status()
        return resp.json()

    @property
    def kernel_id(self) -> str | None:
        return self._kernel_id


def _parse_execute_response(data: dict) -> ExecutionResult:
    """Jupyter-API-Antwort in ExecutionResult umwandeln."""
    result = ExecutionResult()
    outputs = data.get("outputs", [])

    for out in outputs:
        out_type = out.get("output_type", "")
        if out_type == "stream":
            if out.get("name") == "stdout":
                result.stdout += out.get("text", "")
            else:
                result.stderr += out.get("text", "")
        elif out_type in ("execute_result", "display_data"):
            mime = out.get("data", {})
            if "text/plain" in mime:
                result.outputs.append({"type": "text", "content": mime["text/plain"]})
            if "image/png" in mime:
                result.outputs.append({"type": "image", "format": "png"})
        elif out_type == "error":
            result.error = f"{out.get('ename', 'Error')}: {out.get('evalue', '')}"
            result.stderr = "\n".join(out.get("traceback", []))

    result.execution_count = data.get("execution_count", 0)
    return result


# =============================================================================
# JupyterAddOn
# =============================================================================


class JupyterAddOn(AddOn):
    """Code-Ausführung via Jupyter-Kernel.

    Registriert local:jupyter:execute_code beim MCPToolsRouter.
    LLM kann autonom Code ausführen.
    """

    name = "jupyter"
    version = "0.1.0"
    dependencies: list[str] = []

    def __init__(
        self,
        url: str = "http://localhost:8888",
        token: str = "",
        kernel: str = "python3",
        timeout: int = 30,
    ) -> None:
        self._url = url
        self._token = token
        self._kernel_name = kernel
        self._timeout = timeout
        self._client: JupyterClient | None = None

    # -------------------------------------------------------------------------
    # Lifecycle
    # -------------------------------------------------------------------------

    async def on_attach(self, heinzel) -> None:
        self._client = JupyterClient(
            url=self._url,
            token=self._token,
            timeout=self._timeout,
        )
        # Tool beim MCPToolsRouter registrieren
        try:
            router = heinzel.addons.get("mcp_tools_router")
            if router and hasattr(router, "register_local_handler"):
                await router.register_local_handler(
                    address="local:jupyter:execute_code",
                    handler=self._tool_execute,
                    description="Python-Code in Jupyter-Kernel ausführen",
                    input_schema={
                        "type": "object",
                        "properties": {
                            "code": {"type": "string", "description": "Python-Code"},
                        },
                        "required": ["code"],
                    },
                )
        except Exception as exc:
            logger.warning(f"[JupyterAddOn] Tool-Registrierung fehlgeschlagen: {exc}")

        logger.info(f"[JupyterAddOn] bereit — {self._url} (Kernel: {self._kernel_name})")

    async def on_detach(self, heinzel) -> None:
        if self._client:
            await self._client.stop()
            self._client = None

    # -------------------------------------------------------------------------
    # Öffentliche API
    # -------------------------------------------------------------------------

    async def execute(self, code: str) -> ExecutionResult:
        """Code ausführen — Kernel bei Bedarf starten."""
        if not self._client:
            return ExecutionResult(error="JupyterAddOn nicht initialisiert")

        if not self._client.kernel_id:
            try:
                await self._client.start(self._kernel_name)
            except Exception as exc:
                return ExecutionResult(error=f"Kernel-Start fehlgeschlagen: {exc}")

        return await self._client.execute(code)

    async def restart_kernel(self) -> bool:
        """Kernel neu starten — sauberer Zustand."""
        if not self._client:
            return False
        await self._client.stop()
        try:
            await self._client.start(self._kernel_name)
            return True
        except Exception as exc:
            logger.error(f"[JupyterAddOn] Kernel-Restart fehlgeschlagen: {exc}")
            return False

    # -------------------------------------------------------------------------
    # Tool-Handler für MCPToolsRouter
    # -------------------------------------------------------------------------

    async def _tool_execute(self, args: dict) -> str:
        """Tool-Interface: code → ExecutionResult als Text."""
        code = args.get("code", "")
        if not code:
            return "Kein Code angegeben."
        result = await self.execute(code)
        return result.as_text()
