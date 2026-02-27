"""
H.E.I.N.Z.E.L. Provider — Retry-Logik mit Exponential Backoff

Konfigurierbar per provider.yaml:
  retry:
    max_retries: 3
    initial_delay_s: 1.0
    backoff_factor: 2.0
    max_delay_s: 60.0
    retry_on: [429, 500, 502, 503, 504]

Kein Silent-Fail: Aufrufer bekommt immer klare Fehlermeldung.
Rate-Limit-Hits werden als "rate_limit" Status in Metriken geloggt.
"""
import asyncio
import time
from typing import Optional


# Defaults (ueberschreibbar via provider.yaml retry-Sektion)
DEFAULT_RETRY_CONFIG = {
    "max_retries": 3,
    "initial_delay_s": 1.0,
    "backoff_factor": 2.0,
    "max_delay_s": 60.0,
    "retry_on": [429, 500, 502, 503, 504],
}


class RetryExhausted(Exception):
    """Alle Retry-Versuche aufgebraucht."""
    def __init__(self, attempts: int, last_status: int, last_error: str):
        self.attempts = attempts
        self.last_status = last_status
        self.last_error = last_error
        super().__init__(
            f"Nach {attempts} Versuchen fehlgeschlagen "
            f"(letzter Status: {last_status}): {last_error}"
        )


class RateLimitHit(Exception):
    """429 Rate-Limit erreicht, alle Retries erschoepft."""
    def __init__(self, attempts: int, retry_after: Optional[int] = None):
        self.attempts = attempts
        self.retry_after = retry_after
        msg = f"Rate-Limit nach {attempts} Versuchen. "
        if retry_after:
            msg += f"Retry-After: {retry_after}s"
        super().__init__(msg)


def _get_retry_config(provider_config: dict) -> dict:
    """Liest Retry-Config aus provider.yaml, faellt auf Defaults zurueck."""
    yaml_retry = provider_config.get("retry") or {}
    return {**DEFAULT_RETRY_CONFIG, **yaml_retry}


def _get_delay(attempt: int, config: dict, retry_after: Optional[int] = None) -> float:
    """Berechnet Wartezeit fuer Versuch N (1-basiert)."""
    if retry_after and retry_after > 0:
        return min(float(retry_after), config["max_delay_s"])
    delay = config["initial_delay_s"] * (config["backoff_factor"] ** (attempt - 1))
    return min(delay, config["max_delay_s"])


async def with_retry(fn, provider_config: dict,
                     rate_limit_tracker: Optional[list] = None):
    """
    Fuehrt fn() aus mit Retry-Logik.
    fn: async callable ohne Argumente
    rate_limit_tracker: optional list, wird bei 429 um 1 erhoeht
    Raises: RetryExhausted | RateLimitHit | Exception (andere Fehler)
    """
    cfg = _get_retry_config(provider_config)
    max_retries = cfg["max_retries"]
    retry_on = set(cfg["retry_on"])

    last_status = 0
    last_error = ""

    for attempt in range(1, max_retries + 2):  # +1 fuer letzten Versuch
        try:
            return await fn()
        except Exception as e:
            # Status-Code extrahieren
            status = getattr(getattr(e, "response", None), "status_code", 0)
            if status == 0 and hasattr(e, "status_code"):
                status = e.status_code

            last_error = str(e)
            last_status = status

            # Nicht retrybar oder letzter Versuch
            if status not in retry_on or attempt > max_retries:
                if status not in retry_on:
                    raise  # Direkt weiterwerfen
                break  # Max-Retries erschoepft

            # Rate-Limit tracken
            is_rate_limit = status == 429
            if is_rate_limit and rate_limit_tracker is not None:
                rate_limit_tracker.append(time.time())

            # Retry-After Header auslesen
            retry_after = None
            response = getattr(e, "response", None)
            if response and hasattr(response, "headers"):
                try:
                    retry_after = int(response.headers.get("retry-after", 0))
                except (ValueError, TypeError):
                    pass

            delay = _get_delay(attempt, cfg, retry_after)
            await asyncio.sleep(delay)

    # Alle Versuche erschoepft
    if last_status == 429:
        raise RateLimitHit(attempts=max_retries + 1)
    raise RetryExhausted(attempts=max_retries + 1,
                         last_status=last_status, last_error=last_error)
