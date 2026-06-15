"""Migración one-shot de la persistencia CSV/JSON a SQLite.

Importa el histórico existente bajo ``logs/`` a la base de datos (``logs/bot.db``
o ``DB_PATH``) y archiva los archivos originales en ``logs/archive/`` como
respaldo (no los borra). Tolerante a filas corruptas: las salta, igual que hacía
el código de lectura anterior.

Uso:
    python scripts/migrate_csv_to_db.py

Es seguro re-ejecutarlo solo sobre una DB vacía; si ya hay datos importados,
volver a correrlo duplicaría filas (los archivos ya estarán en logs/archive/, así
que en la práctica no se reimporta lo ya migrado).
"""
import csv
import json
import os
import shutil
import sys
from datetime import datetime

# Permite ejecutar el script directamente (python scripts/migrate_csv_to_db.py).
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.db import (  # noqa: E402
    ClosedTrade, EquityPoint, RiskFirstSeen, Signal, SignalMemoryRecord, Trade,
    init_db, session_scope,
)

LOGS = "logs"
ARCHIVE = os.path.join(LOGS, "archive")
# Plataforma por defecto para los CSV sueltos en la raíz de logs/ (sin columna
# `platform` ni subcarpeta). Configurable por si la producción no es MT4.
DEFAULT_PLATFORM = os.environ.get("MIGRATE_PLATFORM", "MT4")
_CSV_FILES = ("signals.csv", "trades.csv", "closed_trades.csv", "equity.csv")


def _f(value):
    """float tolerante (cadena vacía/ilegible -> None)."""
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _ts(value):
    """Parsea timestamp en formato CSV; ante fallo devuelve now()."""
    if not value:
        return datetime.now()
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(value, fmt)
        except ValueError:
            continue
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return datetime.now()


def _read_csv(path):
    if not os.path.exists(path):
        return []
    try:
        with open(path, newline="", encoding="utf-8") as f:
            return list(csv.DictReader(f))
    except (csv.Error, OSError):
        return []


def _has_csv(d):
    return any(os.path.exists(os.path.join(d, f)) for f in _CSV_FILES)


def _sources():
    """Orígenes de CSV como (directorio, etiqueta_plataforma).

    Soporta dos disposiciones:
      * CSV sueltos en la raíz ``logs/`` (la disposición actual) → plataforma
        ``DEFAULT_PLATFORM`` (las filas pueden traer su propia columna ``platform``).
      * Subcarpetas ``logs/<plataforma>/`` (disposición histórica).
    """
    if not os.path.isdir(LOGS):
        return []
    sources = []
    if _has_csv(LOGS):
        sources.append((LOGS, DEFAULT_PLATFORM))
    for name in sorted(os.listdir(LOGS)):
        d = os.path.join(LOGS, name)
        if os.path.isdir(d) and name not in ("agents", "archive") and _has_csv(d):
            sources.append((d, name))
    return sources


def migrate_signals(session, csv_dir, platform):
    n = 0
    for r in _read_csv(os.path.join(csv_dir, "signals.csv")):
        session.add(Signal(
            timestamp=_ts(r.get("timestamp")),
            platform=(r.get("platform") or platform).upper(),
            agent=r.get("agent", ""), symbol=r.get("symbol", ""),
            action=r.get("action", ""), confidence=_f(r.get("confidence")),
            trend=r.get("trend", ""), risk_level=r.get("risk_level", ""),
            entry=_f(r.get("entry")), stop_loss=_f(r.get("stop_loss")),
            take_profit=_f(r.get("take_profit")), reason=r.get("reason", ""),
            trade_id=r.get("trade_id", ""),
            executed=str(r.get("executed", "")).lower() == "true",
        ))
        n += 1
    return n


def migrate_trades(session, csv_dir, platform):
    n = 0
    for r in _read_csv(os.path.join(csv_dir, "trades.csv")):
        session.add(Trade(
            timestamp=_ts(r.get("timestamp")),
            platform=(r.get("platform") or platform).upper(),
            symbol=r.get("symbol", ""), action=r.get("action", ""),
            volume=_f(r.get("volume")), price=_f(r.get("price")),
            stop_loss=_f(r.get("stop_loss")), take_profit=_f(r.get("take_profit")),
            retcode=str(r.get("retcode", "")), order_id=str(r.get("order_id", "")),
            comment=r.get("comment", ""),
        ))
        n += 1
    return n


def migrate_closed_trades(session, csv_dir, platform):
    n = 0
    for r in _read_csv(os.path.join(csv_dir, "closed_trades.csv")):
        dur = r.get("duration_seconds")
        try:
            dur = int(float(dur)) if dur not in (None, "") else None
        except (TypeError, ValueError):
            dur = None
        session.add(ClosedTrade(
            timestamp=_ts(r.get("timestamp")),
            platform=(r.get("platform") or platform).upper(),
            symbol=r.get("symbol", ""), action=r.get("action", ""),
            volume=_f(r.get("volume")), entry_price=_f(r.get("entry_price")),
            exit_price=_f(r.get("exit_price")), pnl=_f(r.get("pnl")),
            commission=_f(r.get("commission")), duration_seconds=dur,
            trade_id=r.get("trade_id", ""), close_reason=r.get("close_reason", ""),
        ))
        n += 1
    return n


def migrate_equity(session, csv_dir, platform):
    n = 0
    for r in _read_csv(os.path.join(csv_dir, "equity.csv")):
        session.add(EquityPoint(
            timestamp=_ts(r.get("timestamp")),
            platform=(r.get("platform") or platform).upper(),
            balance=_f(r.get("balance")), equity=_f(r.get("equity")),
            free_margin=_f(r.get("free_margin")),
        ))
        n += 1
    return n


def _migrate_memory_file(session, path, scope):
    if not os.path.exists(path):
        return 0
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError):
        return 0
    n = 0
    for symbol, records in (data or {}).items():
        for rec in records:
            final = rec.get("final")
            if final is None:  # registros antiguos: terminal si tenían outcome
                final = rec.get("outcome") is not None
            session.add(SignalMemoryRecord(
                scope=scope, symbol=symbol, timestamp=_ts(rec.get("timestamp")),
                action=rec.get("action", "HOLD"),
                confidence=_f(rec.get("confidence")) or 0.0,
                price=_f(rec.get("price")), stop_loss=_f(rec.get("stop_loss")),
                take_profit=_f(rec.get("take_profit")),
                trade_id=rec.get("trade_id", ""), pnl_real=_f(rec.get("pnl_real")),
                outcome=rec.get("outcome"), move_pct=_f(rec.get("move_pct")),
                final=bool(final),
            ))
            n += 1
    return n


def migrate_memory(session):
    n = _migrate_memory_file(session, os.path.join(LOGS, "memory.json"), "global")
    # Memoria por agente: en logs/agents/ (histórico) o suelta en logs/ (plana).
    for base in (os.path.join(LOGS, "agents"), LOGS):
        if not os.path.isdir(base):
            continue
        for name in os.listdir(base):
            if name.endswith("_memory.json"):
                scope = name[:-len("_memory.json")]
                n += _migrate_memory_file(session, os.path.join(base, name), scope)
    return n


def migrate_first_seen(session):
    path = os.path.join(LOGS, "agents", "riskbook_first_seen.json")
    if not os.path.exists(path):
        return 0
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError):
        return 0
    n = 0
    for ticket, seen in (data or {}).items():
        s = _f(seen)
        if s is not None:
            session.merge(RiskFirstSeen(ticket=str(ticket), first_seen=s))
            n += 1
    return n


def _archive(paths):
    """Mueve los archivos originales migrados a logs/archive/ (respaldo)."""
    os.makedirs(ARCHIVE, exist_ok=True)
    for p in paths:
        if os.path.exists(p):
            rel = os.path.relpath(p, LOGS).replace(os.sep, "_")
            shutil.move(p, os.path.join(ARCHIVE, rel))


def main():
    init_db()
    archived = []
    sources = _sources()
    if not sources:
        print("[aviso] no se encontraron CSV (signals/trades/closed_trades/equity) "
              f"ni en {LOGS}/ ni en sus subcarpetas de plataforma.")
    with session_scope() as session:
        for csv_dir, platform in sources:
            counts = {
                "signals": migrate_signals(session, csv_dir, platform),
                "trades": migrate_trades(session, csv_dir, platform),
                "closed_trades": migrate_closed_trades(session, csv_dir, platform),
                "equity": migrate_equity(session, csv_dir, platform),
            }
            label = platform if csv_dir != LOGS else f"{platform} (raíz)"
            print(f"[{label}] " + ", ".join(f"{k}={v}" for k, v in counts.items()))
            for fname in _CSV_FILES:
                archived.append(os.path.join(csv_dir, fname))

        mem = migrate_memory(session)
        fs = migrate_first_seen(session)
        print(f"[memoria] signal_memory={mem}  first_seen={fs}")

    # Archivar solo tras un commit correcto (session_scope hizo commit al salir).
    archived.append(os.path.join(LOGS, "memory.json"))
    for base in (os.path.join(LOGS, "agents"), LOGS):
        if not os.path.isdir(base):
            continue
        for name in os.listdir(base):
            if name.endswith("_memory.json") or name == "riskbook_first_seen.json":
                archived.append(os.path.join(base, name))
    _archive(archived)
    print(f"\nMigración completa. Originales archivados en {ARCHIVE}/")


if __name__ == "__main__":
    main()
