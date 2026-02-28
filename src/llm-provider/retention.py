"""
H.E.I.N.Z.E.L. Provider — Log-Retention und Speichermanagement

Policies (via instance.yaml):
  log_max_age_days    — JSONL-Dateien aelter als N Tage komprimieren/loeschen
  log_max_size_mb     — Gesamtgroesse begrenzen (aelteste zuerst)
  log_compress        — gzip statt loeschen
  metrics_max_age_days — Metriken-Eintraege aelter als N Tage aus DB loeschen
"""
import glob, gzip, os, shutil, sys
from datetime import datetime, timedelta, timezone


def _now():
    return datetime.now(timezone.utc)


def cleanup_logs(log_dir, max_age_days=30, max_size_mb=500, compress=True):
    """
    Bereinigt JSONL-Logs in log_dir.
    Returns: {"compressed": N, "deleted": N, "freed_mb": F}
    """
    compressed = deleted = freed_bytes = 0
    cutoff = _now() - timedelta(days=max_age_days)

    pattern = os.path.join(log_dir, "*.jsonl*")
    files = sorted(glob.glob(pattern), key=os.path.getmtime)

    for filepath in files:
        if filepath.endswith(".gz"):
            continue
        mtime = datetime.fromtimestamp(os.path.getmtime(filepath), tz=timezone.utc)
        if mtime >= cutoff:
            continue
        size = os.path.getsize(filepath)
        if compress:
            gz_path = filepath + ".gz"
            try:
                with open(filepath, "rb") as fin, gzip.open(gz_path, "wb") as fout:
                    shutil.copyfileobj(fin, fout)
                os.remove(filepath)
                gz_size = os.path.getsize(gz_path)
                freed_bytes += size - gz_size
                compressed += 1
                print(f"Retention: komprimiert {os.path.basename(filepath)} "
                      f"({size//1024}KB -> {gz_size//1024}KB)", file=sys.stderr)
            except Exception as e:
                print(f"Retention: Komprimierung-Fehler {filepath}: {e}", file=sys.stderr)
        else:
            try:
                os.remove(filepath)
                freed_bytes += size
                deleted += 1
                print(f"Retention: geloescht {os.path.basename(filepath)}", file=sys.stderr)
            except Exception as e:
                print(f"Retention: Loeschfehler {filepath}: {e}", file=sys.stderr)

    # Groessenlimit: aelteste zuerst entfernen
    if max_size_mb > 0:
        active = sorted(
            [f for f in glob.glob(os.path.join(log_dir, "*.jsonl*"))
             if not f.endswith(".gz")],
            key=os.path.getmtime
        )
        total = sum(os.path.getsize(f) for f in active)
        limit = max_size_mb * 1024 * 1024
        for filepath in active:
            if total <= limit:
                break
            size = os.path.getsize(filepath)
            try:
                os.remove(filepath)
                freed_bytes += size
                total -= size
                deleted += 1
                print(f"Retention (size): geloescht {os.path.basename(filepath)}", file=sys.stderr)
            except Exception as e:
                print(f"Retention: Fehler {filepath}: {e}", file=sys.stderr)

    return {"compressed": compressed, "deleted": deleted,
            "freed_mb": round(freed_bytes / (1024 * 1024), 2)}


async def cleanup_metrics_db(db_type, db_url, max_age_days=90):
    """
    Loescht Metriken-Eintraege aelter als max_age_days aus costs-Tabelle.
    Returns: {"deleted": N}
    """
    from datetime import timezone
    cutoff = (_now() - timedelta(days=max_age_days)).isoformat()
    deleted = 0
    try:
        if db_type == "sqlite":
            import aiosqlite
            async with aiosqlite.connect(db_url) as db:
                cur = await db.execute("DELETE FROM costs WHERE ts < ?", (cutoff,))
                deleted = cur.rowcount
                await db.commit()
        elif db_type == "postgresql":
            import asyncpg
            pool = await asyncpg.create_pool(db_url, min_size=1, max_size=2)
            async with pool.acquire() as conn:
                result = await conn.execute("DELETE FROM costs WHERE ts < $1", cutoff)
                deleted = int(result.split()[-1]) if result else 0
            await pool.close()
        print(f"Retention: {deleted} Metriken-Eintraege (>{max_age_days}d) geloescht", file=sys.stderr)
    except Exception as e:
        print(f"Retention: DB-Cleanup Fehler: {e}", file=sys.stderr)
    return {"deleted": deleted}
