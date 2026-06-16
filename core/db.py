"""Capa de base de datos (SQLite + SQLAlchemy).

Sustituye la persistencia en CSV/JSON de ``logs/`` por una base de datos
embebida (un solo archivo ``logs/bot.db``). El frontend nunca toca la DB:
siempre va por la API, así que no hace falta un servidor de base de datos por
red — basta SQLite en el propio proceso del bot.

Concurrencia: el bot corre en un único proceso con dos hilos que tocan la DB
(el orquestador escribe, el hilo del API lee). Se activa el modo WAL (lectores
concurrentes + un escritor) y ``busy_timeout`` para no fallar con "database is
locked" si coinciden. NO se usa ningún driver async (regla del proyecto: nada
que rompa el modelo de hilos planos del WebSocket).

Los modelos ORM viven aquí (no en ``core/models.py``, que son los modelos de
dominio pydantic). Cada tabla espeja un CSV/JSON del esquema anterior.
"""
import os
from contextlib import contextmanager
from datetime import datetime
from typing import Optional

from core.clock import broker_now

from sqlalchemy import (
    Boolean, DateTime, Float, Index, Integer, String, create_engine, event,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker


# Raíz del proyecto (carpeta que contiene core/, agents/, main.py…). Se usa para
# anclar la ruta de la DB y que NO dependa del directorio de trabajo actual: si el
# bot se arranca desde otra carpeta (acceso directo, tarea programada, ruta
# absoluta a main.py), una ruta relativa crearía un bot.db NUEVO y vacío allí y la
# tasa de éxito/histórico parecería "perderse". Anclándola siempre apunta al mismo
# archivo.
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _db_path() -> str:
    """Ruta del archivo SQLite (configurable vía DB_PATH; default logs/bot.db).

    El default y cualquier DB_PATH relativo se resuelven contra la raíz del
    proyecto (no contra el CWD) para que el bot use SIEMPRE la misma base de datos
    independientemente de desde dónde se lance. Un DB_PATH absoluto se respeta tal cual."""
    configured = os.getenv("DB_PATH", os.path.join("logs", "bot.db"))
    if os.path.isabs(configured):
        return configured
    return os.path.join(_PROJECT_ROOT, configured)


class Base(DeclarativeBase):
    pass


# ----- Modelos (una tabla por dataset del esquema CSV/JSON anterior) -----

class Signal(Base):
    """Señal generada por un agente (antes logs/{platform}/signals.csv)."""
    __tablename__ = "signals"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    timestamp: Mapped[datetime] = mapped_column(DateTime, default=broker_now)
    platform: Mapped[str] = mapped_column(String(16), default="MT4")
    agent: Mapped[str] = mapped_column(String(64), default="")
    symbol: Mapped[str] = mapped_column(String(32), default="")
    action: Mapped[str] = mapped_column(String(8), default="")
    confidence: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    trend: Mapped[str] = mapped_column(String(16), default="")
    risk_level: Mapped[str] = mapped_column(String(16), default="")
    entry: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    stop_loss: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    take_profit: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    reason: Mapped[str] = mapped_column(String, default="")
    trade_id: Mapped[str] = mapped_column(String(64), default="")
    executed: Mapped[bool] = mapped_column(Boolean, default=False)


class Trade(Base):
    """Orden enviada al broker (antes logs/{platform}/trades.csv)."""
    __tablename__ = "trades"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    timestamp: Mapped[datetime] = mapped_column(DateTime, default=broker_now)
    platform: Mapped[str] = mapped_column(String(16), default="MT4")
    symbol: Mapped[str] = mapped_column(String(32), default="")
    action: Mapped[str] = mapped_column(String(8), default="")
    volume: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    price: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    stop_loss: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    take_profit: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    retcode: Mapped[str] = mapped_column(String(32), default="")
    order_id: Mapped[str] = mapped_column(String(64), default="")
    comment: Mapped[str] = mapped_column(String, default="")


class ClosedTrade(Base):
    """Trade cerrado detectado por la mesa (antes closed_trades.csv)."""
    __tablename__ = "closed_trades"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    timestamp: Mapped[datetime] = mapped_column(DateTime, default=broker_now)
    platform: Mapped[str] = mapped_column(String(16), default="MT4")
    symbol: Mapped[str] = mapped_column(String(32), default="")
    action: Mapped[str] = mapped_column(String(8), default="")
    volume: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    entry_price: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    exit_price: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    pnl: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    commission: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    duration_seconds: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    trade_id: Mapped[str] = mapped_column(String(64), default="")
    close_reason: Mapped[str] = mapped_column(String(64), default="")


class EquityPoint(Base):
    """Instantánea de la cartera para el gráfico (antes equity.csv)."""
    __tablename__ = "equity"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    timestamp: Mapped[datetime] = mapped_column(DateTime, default=broker_now)
    platform: Mapped[str] = mapped_column(String(16), default="MT4")
    balance: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    equity: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    free_margin: Mapped[Optional[float]] = mapped_column(Float, nullable=True)


class SignalMemoryRecord(Base):
    """Memoria de señales con evaluación de resultado (antes memory.json y
    logs/agents/{name}_memory.json). ``scope`` = "global" o el nombre del agente."""
    __tablename__ = "signal_memory"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    scope: Mapped[str] = mapped_column(String(64), default="global")
    symbol: Mapped[str] = mapped_column(String(32), default="")
    timestamp: Mapped[datetime] = mapped_column(DateTime, default=broker_now)
    action: Mapped[str] = mapped_column(String(8), default="HOLD")
    confidence: Mapped[float] = mapped_column(Float, default=0.0)
    price: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    stop_loss: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    take_profit: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    trade_id: Mapped[str] = mapped_column(String(64), default="")
    pnl_real: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    outcome: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    move_pct: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    final: Mapped[bool] = mapped_column(Boolean, default=False)


class RiskFirstSeen(Base):
    """Antigüedad de posiciones vista por la mesa (antes riskbook_first_seen.json).
    {ticket: epoch del primer avistamiento} para el período de gracia."""
    __tablename__ = "risk_first_seen"

    ticket: Mapped[str] = mapped_column(String(64), primary_key=True)
    first_seen: Mapped[float] = mapped_column(Float)


class AgentStat(Base):
    """Contadores acumulados por agente (señales/trades/holds) para que el
    resumen del dashboard SOBREVIVA a los reinicios del bot (antes solo vivían en
    memoria y se perdían). Clave = nombre del agente."""
    __tablename__ = "agent_stats"

    agent: Mapped[str] = mapped_column(String(64), primary_key=True)
    signals: Mapped[int] = mapped_column(Integer, default=0)
    trades: Mapped[int] = mapped_column(Integer, default=0)
    holds: Mapped[int] = mapped_column(Integer, default=0)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=broker_now)


# Índices por consulta habitual (plataforma + símbolo + tiempo).
Index("ix_signals_query", Signal.platform, Signal.symbol, Signal.timestamp)
Index("ix_trades_query", Trade.platform, Trade.symbol, Trade.timestamp)
Index("ix_closed_trades_query", ClosedTrade.platform, ClosedTrade.symbol, ClosedTrade.timestamp)
Index("ix_equity_query", EquityPoint.platform, EquityPoint.timestamp)
Index("ix_signal_memory_query", SignalMemoryRecord.scope, SignalMemoryRecord.symbol, SignalMemoryRecord.timestamp)


# ----- Engine y sesiones -----

_engine = None
SessionLocal = None


def _build_engine(url: Optional[str] = None):
    """Crea el engine SQLite con WAL + busy_timeout por conexión."""
    if url is None:
        path = _db_path()
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        url = f"sqlite:///{path}"
    engine = create_engine(
        url,
        connect_args={"check_same_thread": False},
        future=True,
    )

    @event.listens_for(engine, "connect")
    def _set_sqlite_pragmas(dbapi_conn, _record):
        cur = dbapi_conn.cursor()
        # WAL: lectores concurrentes + un escritor (patrón orquestador/API).
        # En :memory: WAL no aplica; SQLite lo ignora sin error.
        cur.execute("PRAGMA journal_mode=WAL")
        cur.execute("PRAGMA busy_timeout=5000")
        cur.execute("PRAGMA synchronous=NORMAL")
        cur.execute("PRAGMA foreign_keys=ON")
        cur.close()

    return engine


def init_db(url: Optional[str] = None):
    """Inicializa el engine y crea el esquema. Idempotente. Se llama una vez al
    arrancar (main.py) antes de levantar orquestador y API. ``url`` permite
    inyectar una DB en memoria en tests (``sqlite:///:memory:``)."""
    global _engine, SessionLocal
    if _engine is not None and url is None:
        return _engine
    _engine = _build_engine(url)
    SessionLocal = sessionmaker(bind=_engine, expire_on_commit=False, future=True)
    Base.metadata.create_all(_engine)
    return _engine


def get_session():
    """Sesión nueva. El llamante es responsable de cerrarla (lecturas del API)."""
    if SessionLocal is None:
        init_db()
    return SessionLocal()


@contextmanager
def session_scope():
    """Sesión transaccional para escrituras cortas: commit/rollback/close."""
    if SessionLocal is None:
        init_db()
    session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


# ----- Contadores por agente (persistencia del resumen del dashboard) -----

def load_agent_stats() -> dict:
    """Devuelve {agent: {"signals", "trades", "holds"}} desde la DB para
    restaurar los contadores al arrancar. Fail-safe: ante error devuelve {}."""
    try:
        with session_scope() as s:
            return {r.agent: {"signals": r.signals, "trades": r.trades, "holds": r.holds}
                    for r in s.query(AgentStat).all()}
    except Exception:
        return {}


def save_agent_stats(stats: dict) -> None:
    """Upsert de los contadores por agente. Fail-safe (no propaga errores: el
    bucle del bot no debe caerse por un fallo de persistencia)."""
    try:
        with session_scope() as s:
            for name, c in (stats or {}).items():
                row = s.get(AgentStat, name)
                if row is None:
                    row = AgentStat(agent=name)
                    s.add(row)
                row.signals = int(c.get("signals", 0) or 0)
                row.trades = int(c.get("trades", 0) or 0)
                row.holds = int(c.get("holds", 0) or 0)
                row.updated_at = broker_now()
    except Exception:
        pass
