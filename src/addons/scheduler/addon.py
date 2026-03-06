"""SchedulerAddOn — Proaktive Tasks via asyncio + Cron-Syntax.

Kein OS-Cron. Läuft im Prozess. Ruft runner.chat() zu definierten Zeiten auf.
Optional: Ergebnis in Mattermost-Channel posten.

Konfiguration (heinzel.yaml):
    addons:
      scheduler:
        jobs:
          - name: morning-briefing
            schedule: '0 8 * * *'
            prompt: 'Erstelle ein Morning Briefing'
            channel: heinzel-briefings   # optional
          - name: weekly-review
            schedule: '0 17 * * 5'
            prompt: 'Was wurde diese Woche erledigt?'

Verwendung:
    addon = SchedulerAddOn(jobs=[
        ScheduledJob(name="test", schedule="* * * * *", prompt="Hallo!")
    ])

Abhängigkeiten: croniter (pip install croniter)
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone

from croniter import croniter

from core.addon import AddOn
from core.models import AddOnResult, PipelineContext, ContextHistory

logger = logging.getLogger(__name__)


# =============================================================================
# Datenmodell
# =============================================================================


@dataclass
class ScheduledJob:
    """Einzelner Cron-Job."""

    name: str
    schedule: str        # Cron-Syntax: '0 8 * * *'
    prompt: str          # wird an runner.chat() übergeben
    channel: str = ""    # optional: Mattermost-Channel für Output
    enabled: bool = True

    def next_run(self, after: datetime | None = None) -> datetime:
        """Nächsten Ausführungszeitpunkt berechnen."""
        base = after or datetime.now(timezone.utc)
        # croniter arbeitet mit naiven Datetimes
        base_naive = base.replace(tzinfo=None)
        cron = croniter(self.schedule, base_naive)
        return cron.get_next(datetime)


# =============================================================================
# SchedulerAddOn
# =============================================================================


class SchedulerAddOn(AddOn):
    """Proaktiver Scheduler — ruft runner.chat() nach Cron-Plan auf.

    Lifecycle:
        on_attach → _loop() als asyncio.Task starten
        on_detach → Task canceln
    """

    name = "scheduler"
    version = "0.1.0"
    dependencies: list[str] = []

    def __init__(self, jobs: list[ScheduledJob] | None = None) -> None:
        self._jobs: list[ScheduledJob] = jobs or []
        self._task: asyncio.Task | None = None
        self._running = False
        self._heinzel = None

    # -------------------------------------------------------------------------
    # Lifecycle
    # -------------------------------------------------------------------------

    async def on_attach(self, heinzel) -> None:
        self._heinzel = heinzel
        self._running = True
        self._task = asyncio.create_task(self._loop(), name="scheduler-loop")
        logger.info(
            f"[SchedulerAddOn] gestartet — {len(self._jobs)} Job(s): "
            f"{[j.name for j in self._jobs]}"
        )

    async def on_detach(self, heinzel) -> None:
        self._running = False
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        self._task = None
        logger.info("[SchedulerAddOn] gestoppt")

    # -------------------------------------------------------------------------
    # Scheduler-Loop
    # -------------------------------------------------------------------------

    async def _loop(self) -> None:
        """Sekündlich prüfen ob ein Job fällig ist."""
        last_run: dict[str, datetime] = {}

        while self._running:
            now = datetime.now(timezone.utc)
            now_naive = now.replace(tzinfo=None)

            for job in self._jobs:
                if not job.enabled:
                    continue

                last = last_run.get(job.name)
                next_dt = job.next_run(last)

                if now_naive >= next_dt:
                    last_run[job.name] = now_naive
                    asyncio.create_task(
                        self._run_job(job),
                        name=f"scheduler-{job.name}"
                    )

            await asyncio.sleep(1)

    async def _run_job(self, job: ScheduledJob) -> None:
        """Einzelnen Job ausführen."""
        logger.info(f"[SchedulerAddOn] Job '{job.name}' startet")
        try:
            runner = getattr(self._heinzel, "runner", self._heinzel)
            response = await runner.chat(job.prompt)

            if job.channel:
                await self._post_to_channel(job.channel, response)

            logger.info(f"[SchedulerAddOn] Job '{job.name}' abgeschlossen")
        except Exception as exc:
            logger.error(f"[SchedulerAddOn] Fehler in Job '{job.name}': {exc}")

    async def _post_to_channel(self, channel: str, text: str) -> None:
        """In Mattermost-Channel posten wenn MattermostAddOn verfügbar."""
        try:
            mm = self._heinzel.addons.get("mattermost")
            if mm:
                await mm.post_to(channel, text)
        except Exception as exc:
            logger.warning(f"[SchedulerAddOn] Mattermost-Post fehlgeschlagen: {exc}")

    # -------------------------------------------------------------------------
    # Job-Verwaltung
    # -------------------------------------------------------------------------

    def add_job(self, job: ScheduledJob) -> None:
        """Job zur Laufzeit hinzufügen."""
        self._jobs.append(job)
        logger.info(f"[SchedulerAddOn] Job '{job.name}' hinzugefügt: {job.schedule}")

    def remove_job(self, name: str) -> bool:
        """Job zur Laufzeit entfernen."""
        before = len(self._jobs)
        self._jobs = [j for j in self._jobs if j.name != name]
        removed = len(self._jobs) < before
        if removed:
            logger.info(f"[SchedulerAddOn] Job '{name}' entfernt")
        return removed

    def list_jobs(self) -> list[dict]:
        return [
            {
                "name": j.name,
                "schedule": j.schedule,
                "prompt": j.prompt[:50],
                "channel": j.channel,
                "enabled": j.enabled,
                "next_run": j.next_run().isoformat(),
            }
            for j in self._jobs
        ]

    @classmethod
    def from_config(cls, cfg: dict) -> "SchedulerAddOn":
        """Aus heinzel.yaml-Section bauen."""
        jobs = []
        for entry in cfg.get("jobs", []):
            jobs.append(ScheduledJob(
                name=entry["name"],
                schedule=entry["schedule"],
                prompt=entry["prompt"],
                channel=entry.get("channel", ""),
                enabled=entry.get("enabled", True),
            ))
        return cls(jobs=jobs)
