"""Coordinador "mesa de dirección" sobre los agentes especialistas.

Dos capas que cooperan:

- ``RiskBook`` (determinista): la tesorería. Calcula la economía de la cartera
  (equity, exposición por símbolo y total, presupuesto) e impone TOPES DUROS
  sobre lo que proponga el coordinador LLM. No depende de ningún LLM: es el
  guardarraíl que evita sobreexponerse pase lo que pase.

- ``CoordinatorAgent`` (LLM): el director. Dado el estado de cartera y las
  señales que le pasan los especialistas, decide go/no-go, prioridad y
  asignación de capital por símbolo, y qué hacer con las posiciones abiertas
  (mantener / reducir / cerrar). Sus números son PROPUESTAS: el RiskBook recorta
  después lo que exceda los límites. Si el LLM falla, cae a una decisión
  determinista (fail-safe), igual que el proveedor de noticias.
"""
import json
import time
from typing import Optional

from core.clock import broker_dt_from_posix
from core.models import BotConfig
from core.strategy import StrategyEngine
from agents.positions import _pos_get, _pos_to_float, _pos_direction


# Grupos de símbolos CORRELACIONADOS (se mueven juntos). Dentro de un grupo NO se
# mantienen direcciones netas opuestas a la vez: ir largo de uno y corto del otro
# es, sin querer, un pairs trade que paga doble coste y queda medio cubierto.
# Grupo fijo BTC/ETH (ETH tiene beta alta respecto a BTC). El match es por PREFIJO
# para tolerar sufijos del bróker (BTCUSDm, ETHUSD.r, etc.).
CORRELATED_GROUPS: list[tuple[str, ...]] = [("BTCUSD", "ETHUSD")]


class RiskBook:
    """Capa determinista de riesgo/capital. Fuente única de verdad de la
    economía de la cartera y guardarraíl de los límites duros."""

    def __init__(self, config: dict):
        self.max_total_exposure_pct = float(config.get("max_total_exposure_pct", 0.5))
        self.max_symbol_allocation_pct = float(config.get("max_symbol_allocation_pct", 0.4))
        self.can_close = bool(config.get("can_close", True))
        # Si False (default), la mesa NO ejecuta la gestión DISCRECIONAL del LLM
        # (reduce/close/hedge propuestos "por criterio"): solo cierra por FUERZA
        # MAYOR (guardias deterministas: hard-stop y reversión). Las posiciones
        # tienen su propio Stop Loss y se respeta. `can_close` sigue siendo el
        # kill-switch global (si es False, ni siquiera las guardias cierran).
        self.llm_can_close = bool(config.get("llm_can_close", False))
        # Control de concentración direccional / reversión de tendencia.
        self.max_net_direction_pct = float(config.get("max_net_direction_pct", 0.6))
        # Piramidación a favor (add-to-winners): tope superior del sesgo neto que se
        # tolera SOLO cuando la posición neta del símbolo va en GANANCIA y la
        # tendencia del especialista confirma la dirección. Permite añadir a una
        # tendencia ganadora más allá de `max_net_direction_pct` sin levantar los
        # techos duros de exposición total/asignación. Default = max_net_direction_pct
        # (sin piramidación extra si no se configura).
        self.max_pyramid_direction_pct = float(
            config.get("max_pyramid_direction_pct", self.max_net_direction_pct))
        self.reversal_drawdown_pct = float(config.get("reversal_drawdown_pct", 0.015))
        self.max_symbol_loss_pct = float(config.get("max_symbol_loss_pct", 0.0))
        # Rango del R:R objetivo (tp_rr) que la mesa puede fijar por entrada para
        # recortar/ampliar el TP del especialista (gobierna la duración objetivo
        # de la operación). Se acota en clamp().
        self.tp_rr_min = float(config.get("tp_rr_min", 1.0))
        self.tp_rr_max = float(config.get("tp_rr_max", 4.0))
        # Rango del multiplicador de lote (size_mult) que la mesa puede aplicar sobre
        # el lote BASE del especialista por convicción/piramidación. Es un lever
        # EXPLÍCITO, separado de allocation_pct (presupuesto): >1 agranda la entrada,
        # <1 la encoge. Se acota en clamp() a [min, max]. Vacío/0 => se respeta el
        # lote base del especialista (×1). Los topes duros de margen/exposición
        # (_fit_volume_to_margin, exposición total/asignación) siguen mandando aguas
        # abajo: el size_mult nunca puede reventarlos.
        self.size_mult_min = float(config.get("size_mult_min", 0.5))
        self.size_mult_max = float(config.get("size_mult_max", 2.0))
        # Nº máximo de posiciones abiertas POR SÍMBOLO que tolera la mesa (0 = sin
        # tope). Lo GOBIERNA la mesa: deriva del perfil de RIESGO (cuántas) modulado
        # por el HORIZONTE (corto = más concurrentes, largo = menos), ambos del
        # front. Es un tope DURO: una entrada aprobada que dejaría el símbolo por
        # encima se veta en clamp() (el LLM lo ve en el prompt y razona dentro de él).
        self.max_open_positions = int(config.get("max_open_positions", 0) or 0)
        # Período de gracia para posiciones recién abiertas (segundos). Mientras la
        # posición más joven de un símbolo no lo supere, las guardias deterministas
        # de reversión y los cierres/reducciones que proponga el LLM se aplazan
        # (se le da tiempo a evolucionar); solo el hard-stop catastrófico lo rompe.
        self.min_hold_seconds = float(config.get("min_hold_seconds", 300.0))
        # Edad de las posiciones MEDIDA POR LA MESA: {ticket: epoch (time.time())
        # del primer avistamiento}. Usar nuestro propio reloj de pared local (no el
        # open_time del bróker) evita depender del desfase de zona horaria del
        # servidor MT, y —a diferencia de time.monotonic()— es PERSISTIBLE: se
        # guarda en la DB (tabla risk_first_seen) y se recarga al arrancar, de modo
        # que el período de gracia SOBREVIVE a los reinicios de la terminal (una
        # posición vista hace 2 h sigue contando 2 h tras reiniciar). Solo en el
        # primer arranque en frío (sin registro) las posiciones preexistentes se
        # ven "recién abiertas" y gozan de gracia una vez (sesgo conservador).
        self._persist_first_seen = bool(config.get("persist_first_seen", False))
        self._first_seen: dict = self._load_first_seen()

    # ----- Persistencia del registro de antigüedad (período de gracia) -----

    def _load_first_seen(self) -> dict:
        """Recarga el registro {ticket: epoch} de la DB si la persistencia está
        activada. Fail-safe: ante cualquier error devuelve un registro vacío."""
        if not self._persist_first_seen:
            return {}
        try:
            from core.db import RiskFirstSeen, get_session
            session = get_session()
            try:
                rows = session.query(RiskFirstSeen).all()
                return {str(r.ticket): float(r.first_seen) for r in rows}
            finally:
                session.close()
        except Exception:
            return {}

    def _save_first_seen(self):
        """Vuelca el registro a la DB. No-op si la persistencia está desactivada
        (p. ej. en tests que solo usan el registro en memoria)."""
        if not self._persist_first_seen:
            return
        try:
            from core.db import RiskFirstSeen, session_scope
            with session_scope() as session:
                session.query(RiskFirstSeen).delete()
                for ticket, seen in self._first_seen.items():
                    session.add(RiskFirstSeen(ticket=str(ticket), first_seen=float(seen)))
        except Exception:
            pass

    # ----- Helpers de dirección (estáticos, reutilizados por el prompt) -----

    @staticmethod
    def _trend_dir(trend) -> Optional[str]:
        """Normaliza la tendencia del especialista a LONG/SHORT (o None si es
        lateral/desconocida). El esquema del LLM usa bullish|bearish|sideways."""
        t = str(trend or "").strip().lower()
        if t in ("bullish", "alcista", "up", "long"):
            return "LONG"
        if t in ("bearish", "bajista", "down", "short"):
            return "SHORT"
        return None

    @staticmethod
    def _side_to_net(side) -> Optional[str]:
        """BUY->LONG, SELL->SHORT (None en otro caso)."""
        s = str(side or "").upper()
        if s == "BUY":
            return "LONG"
        if s == "SELL":
            return "SHORT"
        return None

    @staticmethod
    def _net_to_side(net_direction) -> Optional[str]:
        """LONG->BUY, SHORT->SELL (None si FLAT). Es el lado del libro a tratar."""
        if net_direction == "LONG":
            return "BUY"
        if net_direction == "SHORT":
            return "SELL"
        return None

    @staticmethod
    def _correlated_bases(symbol) -> Optional[tuple[str, ...]]:
        """Bases del grupo correlacionado al que pertenece `symbol`, o None.

        El match es por PREFIJO (case-insensitive) para tolerar sufijos del
        bróker: "BTCUSDm"/"ETHUSD.r" caen en el grupo ("BTCUSD", "ETHUSD")."""
        s = str(symbol or "").upper()
        for group in CORRELATED_GROUPS:
            if any(s.startswith(base) for base in group):
                return group
        return None

    # Orden de "fuerza" de una acción sobre posiciones abiertas: una guardia
    # determinista puede subir la acción pero nunca bajarla.
    _ACTION_RANK = {"hold": 0, "hedge": 1, "reduce": 2, "close": 3}

    @classmethod
    def _stronger_action(cls, a: str, b: str) -> str:
        return a if cls._ACTION_RANK.get(a, 0) >= cls._ACTION_RANK.get(b, 0) else b

    @staticmethod
    def _contract_size(client, symbol: str) -> float:
        """Tamaño de contrato del símbolo. MT4 expone `trade_contract_size`
        el modelo SymbolInfo de MT4 expone `contract_size`. Fallback 1.0 (cripto)."""
        info = client.get_symbol_info(symbol)
        for attr in ("trade_contract_size", "contract_size"):
            v = getattr(info, attr, None)
            if v:
                try:
                    return float(v)
                except (TypeError, ValueError):
                    pass
        return 1.0

    @staticmethod
    def _current_spread(client, symbol: str):
        """Spread actual en puntos (ask-bid)/point, o None si no se puede calcular
        (sin tick o sin `point`). Fail-safe: cualquier error -> None."""
        try:
            tick = client.get_tick(symbol)
            if not tick:
                return None
            info = client.get_symbol_info(symbol)
            point = float(getattr(info, "point", 0) or 0)
            if point <= 0:
                return None
            return (tick.ask - tick.bid) / point
        except Exception:
            return None

    def snapshot(self, client, agents: list, day_start_equity: float = None,
                 in_cooldown: bool = False, day_window_seconds: float = None,
                 day_window_start_ts: float = None) -> dict:
        """Estado económico de la cartera para el dashboard y el coordinador.

        Exposición total = margen usado / equity (la restricción real del bróker).
        Exposición por símbolo = nocional aproximado (volume × precio × contrato)
        sobre el equity, para repartir presupuesto.

        El "P/L del día" (``daily_pnl_pct``) NO es un día natural: se mide desde
        ``day_start_equity`` (el equity al inicio de la ventana móvil de riesgo).
        ``day_window_seconds`` (longitud de esa ventana) y ``day_window_start_ts``
        (epoch en que empezó) se exponen para que el dashboard muestre el rango
        temporal exacto al que aplica."""
        account = client.get_account_info() or {}
        equity = float(account.get("equity") or 0.0)
        balance = float(account.get("balance") or 0.0)
        free_margin = float(account.get("free_margin") or 0.0)
        used_margin = float(account.get("used_margin") or 0.0)
        hedging = bool(account.get("hedging", False))

        all_positions = client.get_positions() or []
        # Reloj de pared local: persistible (ver _first_seen) para que la gracia
        # sobreviva a reinicios. now - first_seen = antigüedad vista por la mesa.
        now_wall = time.time()
        first_seen_before = dict(self._first_seen)
        current_tickets: set = set()
        per_symbol: dict = {}
        for p in all_positions:
            sym = _pos_get(p, "symbol", default="?")
            vol = _pos_to_float(_pos_get(p, "volume"))
            price = _pos_to_float(_pos_get(p, "current_price", "open_price", "price_open"))
            profit = _pos_to_float(_pos_get(p, "profit"))
            direction = _pos_direction(p)
            notional = vol * price * self._contract_size(client, sym)
            d = per_symbol.setdefault(sym, {
                "notional": 0.0, "profit": 0.0, "count": 0,
                "long_notional": 0.0, "short_notional": 0.0,
                "long_vol": 0.0, "short_vol": 0.0,
                "long_count": 0, "short_count": 0,
                "long_profit": 0.0, "short_profit": 0.0,
                "newest_age": None, "oldest_age": None,
            })
            d["notional"] += notional
            d["profit"] += profit
            d["count"] += 1
            # Edad de la posición vista por la mesa (primer avistamiento).
            ticket = _pos_get(p, "ticket")
            if ticket is not None:
                tk = str(ticket)
                current_tickets.add(tk)
                age = now_wall - self._first_seen.setdefault(tk, now_wall)
                if d["newest_age"] is None or age < d["newest_age"]:
                    d["newest_age"] = age
                if d["oldest_age"] is None or age > d["oldest_age"]:
                    d["oldest_age"] = age
            if direction == "BUY":
                d["long_notional"] += notional
                d["long_vol"] += vol
                d["long_count"] += 1
                d["long_profit"] += profit
            elif direction == "SELL":
                d["short_notional"] += notional
                d["short_vol"] += vol
                d["short_count"] += 1
                d["short_profit"] += profit

        # Poda + persistencia SOLO con una lectura de posiciones FIABLE (no vacía).
        # `get_positions()` devuelve `[]` tanto si NO hay posiciones como si la
        # lectura FALLA (bridge MT aún no listo al arrancar, timeout, error del EA).
        # Si podáramos/persistiéramos con un `[]` ESPURIO borraríamos el registro y
        # el período de gracia se REINICIARÍA en cada arranque: `_startup_review()`
        # hace un snapshot antes de que el bridge esté caliente, y ese `[]` espurio
        # arrasaba lo persistido. Con la lista vacía no tocamos nada; los tickets que
        # de verdad se cerraron se podan en la siguiente lectura no vacía que ya no
        # los incluya (autocorrección, sin coste para la edad de los que siguen).
        if all_positions:
            self._first_seen = {t: s for t, s in self._first_seen.items()
                                if t in current_tickets}
            # Persiste solo si el registro cambió (alta de ticket nuevo o poda), para
            # que el período de gracia sobreviva al próximo reinicio.
            if self._first_seen != first_seen_before:
                self._save_first_seen()

        total_exposure_pct = (used_margin / equity) if equity > 0 else 0.0
        daily_pnl_pct = None
        if day_start_equity and day_start_equity > 0:
            daily_pnl_pct = (equity - day_start_equity) / day_start_equity
        # Rango temporal al que aplica el P/L del día (ventana móvil de riesgo).
        daily_pnl_since = None
        if day_window_start_ts:
            try:
                daily_pnl_since = broker_dt_from_posix(
                    day_window_start_ts).isoformat(sep=" ", timespec="seconds")
            except (ValueError, OSError, TypeError):
                daily_pnl_since = None

        # Factor margen/nocional: la exposición POR SÍMBOLO se mide en MARGEN
        # real (como la total = used_margin/equity), no en nocional bruto. El
        # nocional (volume×precio×contrato) de instrumentos apalancados —cripto,
        # índices— es muchas veces el equity y daba %s absurdos (p. ej. 1616%)
        # incomparables con el tope del 40%. Repartimos el margen usado por la
        # cuota de nocional de cada símbolo: sum(margen_símbolo) = used_margin y,
        # con un solo símbolo, su exposición coincide con la total (lo correcto).
        total_notional = sum(d["notional"] for d in per_symbol.values())
        if total_notional > 0 and used_margin > 0:
            margin_factor = used_margin / total_notional
        else:
            # Sin margen real reportado: aproxima por leverage (margen≈nocional/lev).
            leverage = float(account.get("leverage") or 0) or 1.0
            margin_factor = 1.0 / leverage

        empty = {"notional": 0.0, "profit": 0.0, "count": 0,
                 "long_notional": 0.0, "short_notional": 0.0,
                 "long_vol": 0.0, "short_vol": 0.0, "long_count": 0, "short_count": 0,
                 "long_profit": 0.0, "short_profit": 0.0,
                 "newest_age": None, "oldest_age": None}
        symbols = {}
        for agent in agents:
            sym = agent.symbol
            ps = per_symbol.get(sym, empty)
            # Margen aproximado del símbolo = nocional × factor (margen/nocional).
            symbol_margin = ps["notional"] * margin_factor
            used_pct = (symbol_margin / equity) if equity > 0 else 0.0
            # Filtro de spread del especialista (baseline configurado, en puntos) y
            # spread actual en vivo: el director los ve para decidir si fija un
            # `max_spread` por decisión (ajustar el filtro de coste de esa entrada).
            agent_cfg = getattr(agent, "config", None)
            max_spread_filter = float(getattr(agent_cfg, "max_spread_filter", 0.0) or 0.0)
            current_spread = self._current_spread(client, sym)
            # Sesgo neto: nocional de largos - cortos (con signo). FLAT si se netea.
            net_notional = ps["long_notional"] - ps["short_notional"]
            net_volume = round(ps["long_vol"] - ps["short_vol"], 6)
            eps = 1e-9
            if net_notional > eps:
                net_dir = "LONG"
            elif net_notional < -eps:
                net_dir = "SHORT"
            else:
                net_dir = "FLAT"
            symbols[sym] = {
                "exposure_notional": round(ps["notional"], 2),
                "exposure_pct": round(used_pct, 4),
                "gross_exposure_pct": round(used_pct, 4),
                "net_exposure_pct": round((net_notional * margin_factor / equity) if equity > 0 else 0.0, 4),
                "net_volume": net_volume,
                "net_direction": net_dir,
                "long_positions": ps["long_count"],
                "short_positions": ps["short_count"],
                "floating_pnl": round(ps["profit"], 2),
                # P/L flotante DESGLOSADO por lado: en un libro cubierto (largos y
                # cortos a la vez) el neto oculta qué pata sangra y cuál sostiene.
                "long_pnl": round(ps["long_profit"], 2),
                "short_pnl": round(ps["short_profit"], 2),
                "open_positions": ps["count"],
                "newest_position_age": (round(ps["newest_age"], 1)
                                        if ps.get("newest_age") is not None else None),
                "oldest_position_age": (round(ps["oldest_age"], 1)
                                        if ps.get("oldest_age") is not None else None),
                "max_allocation_pct": self.max_symbol_allocation_pct,
                "remaining_pct": round(max(0.0, self.max_symbol_allocation_pct - used_pct), 4),
                # Filtro de spread del especialista (baseline, puntos) + spread vivo,
                # para que el director razone un override `max_spread` por decisión.
                "max_spread_filter": round(max_spread_filter, 1) if max_spread_filter else 0.0,
                "current_spread": (round(current_spread, 1)
                                   if current_spread is not None else None),
            }

        return {
            "equity": round(equity, 2),
            "balance": round(balance, 2),
            "free_margin": round(free_margin, 2),
            "used_margin": round(used_margin, 2),
            "total_exposure_pct": round(total_exposure_pct, 4),
            "max_total_exposure_pct": self.max_total_exposure_pct,
            "max_symbol_allocation_pct": self.max_symbol_allocation_pct,
            "max_net_direction_pct": self.max_net_direction_pct,
            "max_pyramid_direction_pct": self.max_pyramid_direction_pct,
            "size_mult_min": self.size_mult_min,
            "size_mult_max": self.size_mult_max,
            "max_open_positions": self.max_open_positions,
            "reversal_drawdown_pct": self.reversal_drawdown_pct,
            "max_symbol_loss_pct": self.max_symbol_loss_pct,
            "min_hold_seconds": self.min_hold_seconds,
            "hedging": hedging,
            "daily_pnl_pct": round(daily_pnl_pct, 4) if daily_pnl_pct is not None else None,
            "daily_pnl_window_seconds": (round(day_window_seconds)
                                         if day_window_seconds else None),
            "daily_pnl_since": daily_pnl_since,
            "in_cooldown": bool(in_cooldown),
            "open_positions_total": len(all_positions),
            "can_close": self.can_close,
            "symbols": symbols,
        }

    def clamp(self, decisions: list, snapshot: dict, signals: dict = None) -> list:
        """Aplica los topes duros a las decisiones del coordinador.

        Devuelve una lista nueva; cada decisión lleva un campo ``clamp`` legible
        con el ajuste aplicado (o "" si no se tocó). ``signals`` (opcional,
        ``{symbol: signal}``) habilita las guardias que cruzan el sesgo abierto
        con la tendencia nueva; sin él, esas guardias no se evalúan.

        Reglas de entrada:
        - la asignación nunca supera ``max_symbol_allocation_pct``;
        - en cooldown por pérdida diaria no se aprueban entradas;
        - si la exposición total ya alcanza el tope, no se aprueban entradas;
        - si el símbolo ya está en su tope de asignación, no se aprueban entradas;
        - si el símbolo ya está en su máximo de posiciones (``max_open_positions``,
          que gobierna la mesa según perfil de riesgo × horizonte), no se aprueba
          una entrada más;
        - NO se apila en la dirección ya saturada (``max_net_direction_pct``).

        Gestión de posiciones abiertas. Por defecto la mesa solo cierra por
        FUERZA MAYOR: la gestión discrecional del LLM (reduce/close/hedge "por
        criterio") se ignora (-> hold) salvo que ``llm_can_close`` esté activo;
        las posiciones tienen su propio Stop Loss y se respeta. Las guardias
        deterministas SÍ actúan siempre (solo si ``can_close``):
        - hard-stop por símbolo (``max_symbol_loss_pct``) -> close (rompe la gracia);
        - reversión: sesgo abierto vs tendencia nueva con pérdida flotante
          (``reversal_drawdown_pct``) -> reduce (o close si la pérdida es grande),
          fijando ``manage_direction`` al lado a cerrar; SE PAUSA si la posición
          más reciente está dentro del período de gracia (``min_hold_seconds``);
        - período de gracia: un ``reduce``/``close`` propuesto por el LLM sobre una
          posición recién abierta (más joven que ``min_hold_seconds``) se aplaza a
          ``hold`` — se le da tiempo a evolucionar; solo el hard-stop lo salta;
        - ``hedge`` se degrada a ``reduce`` si la cuenta no es hedging o si la
          exposición total ya está en el tope; a ``hold`` si ``can_close`` está off.
        """
        symbols = snapshot.get("symbols", {})
        total_exposure = snapshot.get("total_exposure_pct", 0.0) or 0.0
        max_total = self.max_total_exposure_pct
        in_cooldown = snapshot.get("in_cooldown", False)
        hedging = bool(snapshot.get("hedging", False))
        equity = snapshot.get("equity", 0.0) or 0.0
        signals = signals or {}

        out = []
        for raw in decisions:
            d = dict(raw)
            notes = []
            sym = d.get("symbol")
            approve = bool(d.get("approve"))
            action = str(d.get("position_action", "hold") or "hold").lower()
            alloc = float(d.get("allocation_pct") or 0.0)
            manage_direction = d.get("manage_direction")
            tp_rr = float(d.get("tp_rr") or 0.0)
            size_mult = float(d.get("size_mult") or 0.0)
            max_spread = float(d.get("max_spread") or 0.0)

            sym_info = symbols.get(sym, {})
            net_direction = sym_info.get("net_direction", "FLAT")
            net_side = self._net_to_side(net_direction)
            net_exposure_pct = sym_info.get("net_exposure_pct", 0.0) or 0.0
            floating_pnl = sym_info.get("floating_pnl", 0.0) or 0.0
            open_positions = sym_info.get("open_positions", 0) or 0
            loss_pct = (-floating_pnl / equity) if (equity > 0 and floating_pnl < 0) else 0.0
            # Período de gracia: la posición más reciente del símbolo es demasiado
            # joven para tocarla (salvo emergencia hard-stop).
            newest_age = sym_info.get("newest_position_age")
            in_grace = (self.min_hold_seconds > 0 and newest_age is not None
                        and newest_age < self.min_hold_seconds)

            sig = signals.get(sym) or {}
            entry_side = str(sig.get("action", "")).upper()
            entry_net = self._side_to_net(entry_side)
            trend_dir = self._trend_dir(sig.get("trend"))

            # 1) Asignación: nunca supera el tope del símbolo.
            if alloc < 0:
                alloc = 0.0
            if alloc > self.max_symbol_allocation_pct:
                notes.append(f"asignación {alloc:.0%}->{self.max_symbol_allocation_pct:.0%} (tope símbolo)")
                alloc = self.max_symbol_allocation_pct

            # 1b) R:R objetivo (tp_rr): si la mesa lo informó (>0), acotarlo al
            # rango configurado. 0 => no ajustar (se respeta el TP del especialista).
            if tp_rr > 0:
                clamped_rr = min(max(tp_rr, self.tp_rr_min), self.tp_rr_max)
                if clamped_rr != tp_rr:
                    notes.append(f"tp_rr 1:{tp_rr:.2f}->1:{clamped_rr:.2f} "
                                 f"(rango {self.tp_rr_min:.1f}-{self.tp_rr_max:.1f})")
                tp_rr = clamped_rr

            # 1c) Multiplicador de lote (size_mult): si la mesa lo informó (>0),
            # acotarlo al rango configurado. 0 => no ajustar (lote base ×1). Los
            # topes de margen/exposición aguas abajo siguen mandando.
            if size_mult > 0:
                clamped_mult = min(max(size_mult, self.size_mult_min), self.size_mult_max)
                if clamped_mult != size_mult:
                    notes.append(f"size_mult {size_mult:.2f}x->{clamped_mult:.2f}x "
                                 f"(rango {self.size_mult_min:.2f}-{self.size_mult_max:.2f})")
                size_mult = clamped_mult

            # 1d) Filtro de spread (max_spread): si la mesa lo informó (>0), recortar
            # negativos a 0 (transitorio: aplica solo a la entrada de esta decisión, no
            # toca el baseline del símbolo). Sin tope superior: las unidades de spread
            # varían por símbolo. 0/ausente => baseline configurado del especialista.
            if max_spread < 0:
                max_spread = 0.0
            if max_spread > 0:
                baseline_spread = float(sym_info.get("max_spread_filter") or 0.0)
                if baseline_spread > 0 and abs(max_spread - baseline_spread) > 1e-9:
                    verbo = "afloja" if max_spread > baseline_spread else "aprieta"
                    notes.append(f"max_spread {verbo} {baseline_spread:.1f}->{max_spread:.1f} pts")

            # --- Guardias deterministas sobre las posiciones abiertas ---
            forced = False
            if open_positions > 0 and self.can_close:
                # 2) Hard-stop por símbolo (independiente de la tendencia).
                if self.max_symbol_loss_pct > 0 and loss_pct >= self.max_symbol_loss_pct:
                    action = self._stronger_action(action, "close")
                    manage_direction = net_side
                    forced = True
                    notes.append(f"hard-stop símbolo: pérdida {loss_pct:.1%} >= "
                                 f"{self.max_symbol_loss_pct:.1%} -> {action}")
                # 3) Reversión: conflicto sesgo-vs-tendencia con pérdida flotante.
                elif (self.reversal_drawdown_pct > 0 and trend_dir is not None
                        and net_direction in ("LONG", "SHORT")
                        and trend_dir != net_direction
                        and loss_pct >= self.reversal_drawdown_pct):
                    if in_grace:
                        # Recién abierta: se le da tiempo a evolucionar antes de
                        # forzar la reversión.
                        notes.append(f"reversión en pausa: posición reciente "
                                     f"({newest_age:.0f}s < {self.min_hold_seconds:.0f}s "
                                     f"de gracia)")
                    else:
                        needed = "close" if loss_pct >= 2 * self.reversal_drawdown_pct else "reduce"
                        action = self._stronger_action(action, needed)
                        manage_direction = net_side
                        forced = True
                        notes.append(f"reversión: libro {net_direction} vs tendencia "
                                     f"{trend_dir.lower()}, pérdida {loss_pct:.1%} -> {action}")

            # 3b) Período de gracia: un reduce/close que proponga el LLM sobre una
            # posición recién abierta se aplaza (se le da tiempo a evolucionar).
            # Solo el hard-stop/reversión forzados (forced=True) lo saltan.
            if action in ("reduce", "close") and in_grace and not forced and self.can_close:
                notes.append(f"posición reciente ({newest_age:.0f}s < "
                             f"{self.min_hold_seconds:.0f}s de gracia): {action} aplazado -> hold")
                action = "hold"
                manage_direction = None

            # 3c) Fuerza mayor: salvo que se habilite explícitamente
            # (COORDINATOR_LLM_CAN_CLOSE), la mesa NO ejecuta la gestión
            # DISCRECIONAL del LLM (reduce/close/hedge "por criterio", p. ej. por
            # exposición) sobre posiciones que ya tienen su propio Stop Loss.
            # Solo las guardias deterministas (hard-stop / reversión, forced=True)
            # pueden tocar lo abierto.
            if (action in ("reduce", "close", "hedge") and not forced
                    and not self.llm_can_close):
                notes.append(f"{action} discrecional del LLM ignorado: la mesa solo "
                             f"cierra por fuerza mayor (S/L propio respetado)")
                action = "hold"
                manage_direction = None

            # 4) Cobertura (hedge): degradar según cuenta/exposición.
            if action == "hedge":
                if not self.can_close:
                    action = "hold"
                    notes.append("cobertura desactivada (COORDINATOR_CAN_CLOSE=false)")
                elif open_positions == 0 or net_direction == "FLAT":
                    action = "hold"
                    notes.append("sin posición neta que cubrir -> hold")
                elif not hedging:
                    action = "reduce"
                    manage_direction = net_side
                    notes.append("cuenta sin hedging: cobertura -> reduce")
                elif total_exposure >= max_total:
                    action = "reduce"
                    manage_direction = net_side
                    notes.append(f"exposición total {total_exposure:.0%} >= tope: cobertura -> reduce")
                else:
                    manage_direction = net_side  # lado a neutralizar (se abre el opuesto)

            # 5) close/reduce propuestos por el LLM requieren can_close.
            if action in ("close", "reduce") and not self.can_close:
                notes.append("cierre desactivado (COORDINATOR_CAN_CLOSE=false)")
                action = "hold"
                manage_direction = None

            # --- Vetos de entrada ---
            if approve and in_cooldown:
                notes.append("cooldown pérdida diaria: entrada vetada")
                approve = False

            if approve and total_exposure >= max_total:
                notes.append(f"exposición total {total_exposure:.0%} >= tope {max_total:.0%}: entrada vetada")
                approve = False

            if approve and sym_info.get("remaining_pct", 1.0) <= 0:
                notes.append("símbolo en su tope de asignación: entrada vetada")
                approve = False

            # Tope DURO de nº de posiciones por símbolo (lo gobierna la mesa, deriva
            # del perfil de riesgo × horizonte): no se abre una más si el símbolo ya
            # está en su máximo. Es un guardarraíl de recuento (independiente de la
            # exposición): el LLM lo ve en el prompt y decide dentro de él.
            if (approve and self.max_open_positions > 0
                    and open_positions >= self.max_open_positions):
                notes.append(f"símbolo en su máximo de posiciones "
                             f"({open_positions}/{self.max_open_positions}): entrada vetada")
                approve = False

            # Anti-apilamiento: no añadir más en la dirección neta ya saturada.
            # EXCEPCIÓN (piramidar ganadores / add-to-winners): si la posición neta
            # del símbolo va en GANANCIA y la tendencia del especialista CONFIRMA la
            # dirección, se tolera seguir apilando hasta `max_pyramid_direction_pct`
            # (> max_net_direction_pct). Nunca se piramidan perdedores ni contra
            # tendencia, y los techos duros de exposición total/asignación (vetos
            # anteriores) siguen mandando.
            if (approve and entry_net is not None and entry_net == net_direction
                    and abs(net_exposure_pct) >= self.max_net_direction_pct):
                pyramiding = (floating_pnl > 0 and trend_dir == net_direction
                              and abs(net_exposure_pct) < self.max_pyramid_direction_pct)
                if pyramiding:
                    notes.append(f"piramidando ganador: neto {net_direction} "
                                 f"{abs(net_exposure_pct):.0%} en ganancia y tendencia "
                                 f"{trend_dir.lower()} confirma (tope {self.max_pyramid_direction_pct:.0%})")
                else:
                    notes.append(f"sesgo neto {net_direction} {abs(net_exposure_pct):.0%} >= "
                                 f"tope {self.max_net_direction_pct:.0%}: no apilar")
                    approve = False

            # En reversión/hard-stop forzado, no abrir en la dirección que se está cortando.
            if forced and approve and entry_net is not None and entry_net == net_direction:
                notes.append("posición en reversión: no se añade en esa dirección")
                approve = False

            d["approve"] = approve
            d["allocation_pct"] = round(alloc, 4)
            d["position_action"] = action
            d["tp_rr"] = round(tp_rr, 2) if tp_rr > 0 else 0.0
            d["size_mult"] = round(size_mult, 2) if size_mult > 0 else 0.0
            d["max_spread"] = round(max_spread, 1) if max_spread > 0 else 0.0
            if manage_direction:
                d["manage_direction"] = manage_direction
            d["clamp"] = "; ".join(notes)
            out.append(d)

        # Coherencia entre símbolos correlacionados (BTC/ETH): veta abrir la pata
        # opuesta dentro del grupo. Se aplica al final, sobre las decisiones ya
        # clampadas, porque cruza información ENTRE símbolos.
        self._apply_correlated_group_guard(out, symbols, signals)
        return out

    def _apply_correlated_group_guard(self, out: list, symbols: dict, signals: dict):
        """Veta entradas que abrirían direcciones netas OPUESTAS dentro de un grupo
        de símbolos correlacionados (p. ej. BTC/ETH). Mutación in situ de ``out``.

        Determinista, post-pass sobre las decisiones ya clampadas:
        - la dirección DOMINANTE del grupo la fija la exposición ABIERTA combinada
          (suma de ``net_exposure_pct`` con signo de todos sus símbolos); si el
          grupo está plano, gana la entrada aprobada de mayor confianza;
        - toda entrada aprobada que vaya CONTRA esa dirección se veta (-> hold).

        Nunca cierra ni toca posiciones abiertas (cada una tiene su propio Stop
        Loss): solo bloquea ABRIR la pata opuesta del par."""
        by_symbol = {d.get("symbol"): d for d in out}
        seen: set = set()
        for d in out:
            sym = d.get("symbol")
            bases = self._correlated_bases(sym)
            if bases is None or sym in seen:
                continue
            members = [s for s in by_symbol if self._correlated_bases(s) == bases]
            seen.update(members)
            if len(members) < 2:
                continue

            # Dirección dominante por exposición ABIERTA combinada del grupo.
            group_net = sum(float(symbols.get(m, {}).get("net_exposure_pct", 0.0) or 0.0)
                            for m in members)
            eps = 1e-9
            group_dir = "LONG" if group_net > eps else "SHORT" if group_net < -eps else "FLAT"

            # Entradas aprobadas del grupo con su dirección y confianza.
            approved = []
            for m in members:
                if not by_symbol[m].get("approve"):
                    continue
                sig = signals.get(m) or {}
                ent = self._side_to_net(str(sig.get("action", "")).upper())
                if ent is not None:
                    approved.append((m, ent, float(sig.get("confidence") or 0.0)))
            if not approved:
                continue

            # CONSENSO DEL GRUPO: dos o más entradas aprobadas que COINCIDEN en
            # dirección => el grupo gira de forma coherente; se permiten todas, AUNQUE
            # eso revierta una posición abierta en sentido contrario (de esa posición
            # vieja se ocupa la guardia de reversión por símbolo, no este guard). Antes
            # la dirección dominante la fijaba SOLO la exposición abierta combinada, y
            # un consenso de reversión (ambos especialistas giran a la vez) quedaba
            # vetado por una posición rancia: oportunidad de señal perdida. No es un
            # pairs trade involuntario porque ambas patas nuevas van al MISMO lado.
            entry_dirs = {ent for _m, ent, _c in approved}
            if len(approved) >= 2 and len(entry_dirs) == 1:
                continue

            # DISCREPANCIA (largo de uno, corto del otro) o una sola entrada opuesta al
            # libro: ESO sí es el pairs trade a evitar. El libro abierto manda; si está
            # plano, gana la entrada más fiable. La pata opuesta se veta.
            if group_dir in ("LONG", "SHORT"):
                winner = group_dir
            else:
                winner = max(approved, key=lambda x: x[2])[1]

            for m, ent, _conf in approved:
                if ent == winner:
                    continue
                dd = by_symbol[m]
                dd["approve"] = False
                others = ", ".join(sorted(x for x in members if x != m))
                note = (f"grupo correlacionado ({'/'.join(bases)}): entrada {ent} "
                        f"opuesta a la dirección {winner} del grupo [{others}] -> vetada")
                dd["clamp"] = f"{dd['clamp']}; {note}" if dd.get("clamp") else note


COORDINATOR_SYSTEM_PROMPT = """Eres el director de riesgo y capital de una mesa de trading (una \
"empresa de broker"). Por debajo tienes agentes especialistas, uno por símbolo, que ya han \
analizado su mercado y te proponen una señal (buy/sell/hold con su confianza y niveles). Tu \
trabajo NO es volver a analizar indicadores: es decidir, a nivel de CARTERA, en qué símbolos \
conviene entrar ahora, con qué prioridad y cuánto capital asignar, controlando la exposición \
global para no sobreexponerte y buscando el mejor retorno ajustado al riesgo.

Reglas:
- Decide solo sobre los símbolos que te llegan. Para cada uno indica: approve (true/false),
  priority (1 = más prioritario), allocation_pct (fracción del equity a asignar, entre 0 y 1) y
  position_action sobre las posiciones ABIERTAS de ese símbolo: "hold" (no tocar), "reduce"
  (recortar exposición), "close" (cerrar todo) o "hedge" (cubrir abriendo en sentido contrario
  para neutralizar el riesgo SIN cerrar). Opcionalmente, tp_rr.
- OBJETIVO DE BENEFICIO (tp_rr): para una entrada que apruebas puedes fijar tp_rr = la relación
  riesgo/beneficio OBJETIVO de la operación. Recorta (objetivo más cercano = la operación se
  cierra antes, ROTACIÓN MÁS RÁPIDA) o amplía el TP que propuso el especialista, manteniendo su
  Stop Loss. Si buscas rotar rápido y la tendencia es de corto recorrido, usa valores bajos
  (~1.0–1.5); si hay un movimiento amplio y claro a favor, súbelo. Si lo dejas vacío (o 0) se
  respeta el TP del especialista. El SL NO se toca; tp_rr solo mueve el objetivo de beneficio.
- TAMAÑO DE POSICIÓN (size_mult): para una entrada que apruebas puedes fijar size_mult = un
  multiplicador EXPLÍCITO sobre el lote base que calcula el especialista. >1 agranda la entrada
  (más convicción / piramidar una tendencia ganadora), <1 la encoge (menos convicción, mercado
  dudoso). 1 (o vacío/0) = lote base sin tocar. Es independiente de allocation_pct: allocation_pct
  es el PRESUPUESTO de equity que reservas al símbolo; size_mult es el TAMAÑO de esta entrada en
  concreto. Súbelo cuando la señal es de alta confianza, el rendimiento del agente es bueno y/o
  piramidas un ganador a favor; bájalo en señales flojas o cartera ya tensionada. Una capa de
  riesgo posterior lo acota al rango permitido y, pase lo que pase, el margen libre y los topes de
  exposición recortan el lote final: nunca podrás sobrepasar los límites duros con size_mult.
- FILTRO DE SPREAD (max_spread): cada símbolo tiene un filtro de spread BASE (en puntos) que veta
  abrir si el coste de entrada es excesivo; el contexto te muestra el spread ACTUAL y ese filtro
  máx. por símbolo. Para una entrada que apruebas puedes fijar max_spread = el umbral de spread (en
  puntos) a exigir SOLO en esta entrada: SÚBELO si el spread está temporalmente ensanchado pero la
  señal compensa el coste extra (no perder una buena entrada por un pico de spread), o BÁJALO para
  ser más estricto en baja liquidez / antes de un dato de alto impacto. Es transitorio: no cambia
  la configuración del símbolo, solo el filtro de ESTA entrada. Vacío (o 0) = se respeta el filtro
  base del especialista.
- APETITO POR DEFECTO: una señal accionable (buy/sell) que te llega YA pasó los filtros de
  confianza y riesgo/beneficio del especialista; representa una ventaja real. Tu postura por
  defecto ante ella es APROBAR. No la rechaces por cautela genérica: solo di no si hay una razón
  concreta de CARTERA para ello (la exposición total ya está cerca de su tope, el símbolo ya está
  en su asignación máxima, hay cooldown por pérdida diaria, o entrar apilaría más en una dirección
  neta ya saturada / en conflicto con la tendencia). Si no concurre ninguna de esas, aprueba.
- Reparte el capital, no lo concentres todo en un símbolo, pero tener margen libre disponible es
  una oportunidad desaprovechada: usa una allocation_pct acorde a la confianza de la señal en vez
  de asignar de menos por defecto.
- Nº MÁXIMO DE POSICIONES POR SÍMBOLO: el contexto indica el tope de posiciones abiertas que
  toleras en cada símbolo (lo fija tu configuración de riesgo y horizonte). NO apruebes una entrada
  nueva en un símbolo que ya esté en ese máximo (marcado "EN SU MÁXIMO"): primero tendría que
  cerrarse alguna. Mientras quede hueco, el recuento no es razón para vetar. (La capa de riesgo
  veta igualmente la entrada que rebase el tope, pero respétalo tú primero.)
- Prioriza señales de mayor confianza y mejor relación riesgo/beneficio, y los agentes con mejor
  rendimiento reciente.
- CONCENTRACIÓN DIRECCIONAL: vigila el "sesgo abierto" (neto LONG/SHORT) de cada símbolo. NO
  apiles más posiciones en una dirección ya muy cargada. Si el libro está claramente sesgado en
  una dirección y la TENDENCIA del especialista gira en contra (marcado como ⚠ CONFLICTO), no
  añadas en esa dirección y protege la pérdida: usa "reduce"/"close" del lado perdedor, o "hedge"
  si conviene mantener las posiciones pero frenar la sangría.
- SÍMBOLOS CORRELACIONADOS (BTCUSD y ETHUSD se mueven juntos): NO los abras en direcciones
  opuestas a la vez (largo de uno y corto del otro). Es un pairs trade involuntario que paga doble
  coste y queda medio cubierto. Si sus especialistas discrepan en dirección, aprueba SOLO el de
  mayor confianza/mejor rendimiento y deja el otro en hold. (La capa de riesgo veta igualmente la
  pata opuesta del par, pero decídelo tú primero.)
- PIRAMIDAR GANADORES (add-to-winners): cuando un símbolo YA tiene posición neta EN GANANCIA y el
  especialista CONFIRMA la continuación de la tendencia en esa misma dirección, NO te quedes en
  "hold": considera APROBAR una entrada adicional a favor (piramidar) con una allocation_pct
  incremental y un size_mult > 1, y sube el tp_rr para dejar correr la tendencia. Es la forma de exprimir una
  tendencia clara. Disciplina estricta: solo se piramida lo que va en GANANCIA y A FAVOR; nunca se
  añade a una posición en pérdida ni contra la tendencia. La capa de riesgo tolera este apilamiento
  hasta un tope superior solo en ese caso (ganador + tendencia confirma).
- "hedge" solo tiene sentido si la cuenta permite cobertura (ver "Cobertura disponible" en el
  contexto); si no, la capa de riesgo lo convertirá en "reduce".
- Si la cartera ya está muy expuesta o en pérdidas del día, sé conservador GESTIONANDO LAS
  ENTRADAS: no apruebes operaciones nuevas. NO cierres/reduzcas posiciones abiertas solo por
  "controlar exposición": cada posición ya tiene su Stop Loss y se respeta. Reserva reduce/close
  para un CONFLICTO CLARO de reversión (el libro va en una dirección y la tendencia gira en
  contra con pérdida real). Gestionar exposición = abrir menos, NO cerrar lo que ya está.
- PACIENCIA CON LO RECIÉN ABIERTO: una posición acaba de abrirse no se cierra a la primera de
  cambio. Fíjate en la "Antigüedad posiciones": si está marcada "EN PERÍODO DE GRACIA", déjala
  evolucionar (usa "hold") salvo emergencia clara; la capa de riesgo aplazará igualmente los
  reduce/close prematuros sobre posiciones demasiado jóvenes.
- Una señal "hold" del especialista normalmente NO se aprueba como entrada nueva.
- Tus números son propuestas: una capa de riesgo posterior recortará lo que exceda los límites
  duros (incluidos topes de dirección neta y guardias de reversión). Aun así, respeta los topes
  que aparecen en el contexto.
- IDIOMA: redacta SIEMPRE los textos libres ("rationale" y "reason") en español (castellano),
  nunca en inglés.

Responde SOLO con JSON válido, sin texto adicional:
{
  "rationale": "razón global breve EN ESPAÑOL de tus decisiones de cartera",
  "decisions": [
    {"symbol": "BTCUSD", "approve": true, "priority": 1, "allocation_pct": 0.25,
     "position_action": "hold", "tp_rr": 1.5, "size_mult": 1.2, "max_spread": 60,
     "reason": "explicación breve EN ESPAÑOL"}
  ]
}
position_action admite: "hold" | "reduce" | "close" | "hedge". tp_rr es opcional (omítelo o 0
para respetar el TP del especialista). size_mult es opcional (omítelo o 1 para el lote base del
especialista; >1 agranda, <1 encoge). max_spread es opcional (omítelo o 0 para respetar el filtro
de spread base del especialista; en puntos, solo para esta entrada)."""


class CoordinatorAgent:
    """Meta-agente LLM que coordina a los especialistas a nivel de cartera."""

    def __init__(self, provider: str, model: str, risk_book: RiskBook,
                 temperature: float = 0.2, debug_mode: bool = True):
        self.provider = provider
        self.model = model
        self.risk_book = risk_book
        self.debug_mode = debug_mode
        config = BotConfig(model=model, debug_mode=debug_mode)
        self.engine = StrategyEngine(config, provider=provider, temperature=temperature)
        self.last_rationale = ""
        # Directiva de apetito (perfil de riesgo + horizonte) inyectada en el prompt
        # de la mesa para que el LLM director cambie REALMENTE su disposición. La
        # fija el orquestador (_refresh_trading_directives); vacía = sin sesgo.
        self.risk_directive = ""
        # Nota de DIRECCIÓN: instrucción libre del responsable (la fija el asistente
        # desde el chat cuando el usuario se lo pide) que el director pondera en sus
        # decisiones de las siguientes rotaciones. La fija el orquestador
        # (set_director_note, persistida en .env DIRECTOR_NOTE); vacía = sin nota.
        self.director_note = ""

    def set_model(self, provider: str, model: str) -> dict:
        """Cambia el provider/modelo LLM del director EN CALIENTE.

        El provider vive en el StrategyEngine y el modelo en su BotConfig
        (``_call_ai`` lee ``config.model``), así que reconstruimos el motor —igual
        que ``SymbolAgent.apply_params`` con los agentes— preservando la temperatura
        actual. La ``risk_directive`` vive en este objeto y se re-inyecta sola en
        ``decide()``, no se pierde. Surte efecto en la próxima coordinación. Lanza
        ValueError si faltan datos."""
        provider = (provider or "").lower().strip()
        model = (model or "").strip()
        if not provider or not model:
            raise ValueError("provider y model son obligatorios")
        temperature = getattr(self.engine, "temperature", 0.2)
        config = BotConfig(model=model, debug_mode=self.debug_mode)
        self.engine = StrategyEngine(config, provider=provider, temperature=temperature)
        self.provider = provider
        self.model = model
        return {"provider": provider, "model": model}

    # ----- API principal -----

    def decide(self, snapshot: dict, signals: dict, agents_overview: dict,
               news_context: str = "") -> dict:
        """Devuelve ``{'rationale': str, 'decisions': [...]}`` ya acotado por el
        RiskBook. ``signals`` es ``{symbol: signal_dict}`` de este ciclo. Si el
        LLM falla o el JSON es inválido, cae a una decisión determinista."""
        raw = None
        if signals or snapshot.get("open_positions_total"):
            system = COORDINATOR_SYSTEM_PROMPT
            if self.risk_directive:
                system = f"{system}\n\n--- Apetito de la mesa (perfil activo) ---\n{self.risk_directive}"
            if self.director_note:
                system = (f"{system}\n\n--- NOTA DE LA DIRECCIÓN (instrucción del responsable; tenla MUY "
                          f"en cuenta al decidir, salvo que choque con un tope de riesgo) ---\n{self.director_note}")
            raw = self.engine.chat_json(
                system,
                self._build_user_prompt(snapshot, signals, agents_overview, news_context),
            )
        parsed = self._parse(raw) if raw else None
        if parsed is None:
            rationale = "fallback determinista (LLM no disponible o respuesta inválida)"
            decisions = self._fallback(signals)
        else:
            rationale, decisions = parsed

        # Garantiza una decisión por símbolo con posiciones abiertas, para que las
        # guardias deterministas del RiskBook puedan actuar aunque el LLM lo omita.
        decisions = self._ensure_coverage(decisions, snapshot)
        clamped = self.risk_book.clamp(decisions, snapshot, signals)
        self.last_rationale = rationale
        return {"rationale": rationale, "decisions": clamped}

    @staticmethod
    def _ensure_coverage(decisions: list, snapshot: dict) -> list:
        """Añade una decisión `hold` por cada símbolo con posiciones abiertas que
        no aparezca ya en `decisions`, para que el clamp pueda protegerlo."""
        covered = {d.get("symbol") for d in decisions}
        extra = []
        for sym, si in (snapshot.get("symbols") or {}).items():
            if sym not in covered and (si.get("open_positions", 0) or 0) > 0:
                extra.append({
                    "symbol": sym, "approve": False, "priority": 99,
                    "allocation_pct": 0.0, "position_action": "hold",
                    "reason": "(sin señal; gestión de posiciones abiertas)",
                })
        return decisions + extra

    # ----- Prompt -----

    def _build_user_prompt(self, snapshot: dict, signals: dict,
                           agents_overview: dict, news_context: str) -> str:
        lines = ["=== ESTADO DE CARTERA ==="]
        lines.append(f"Equity: {snapshot.get('equity', 0)} | "
                     f"Balance: {snapshot.get('balance', 0)} | "
                     f"Margen libre: {snapshot.get('free_margin', 0)}")
        lines.append(f"Exposición total: {snapshot.get('total_exposure_pct', 0):.1%} "
                     f"(tope {snapshot.get('max_total_exposure_pct', 0):.0%})")
        lines.append(f"Tope de asignación por símbolo: {snapshot.get('max_symbol_allocation_pct', 0):.0%}"
                     f" | Tope de dirección neta por símbolo: {snapshot.get('max_net_direction_pct', 0):.0%}")
        max_pos = snapshot.get("max_open_positions", 0) or 0
        if max_pos > 0:
            lines.append(f"Máximo de posiciones abiertas POR SÍMBOLO: {max_pos} "
                         f"(tope duro; no apruebes una entrada en un símbolo que ya esté en su máximo)")
        lines.append(f"Multiplicador de lote (size_mult) permitido: "
                     f"{snapshot.get('size_mult_min', 0.5):.2f}x – {snapshot.get('size_mult_max', 2.0):.2f}x "
                     f"(1x = lote base del especialista)")
        lines.append(f"Cobertura (hedge) disponible en la cuenta: {'sí' if snapshot.get('hedging') else 'no'}")
        if not self.risk_book.llm_can_close:
            lines.append("POLÍTICA: la mesa solo cierra por fuerza mayor. Tus reduce/close/hedge "
                         "discrecionales se IGNORARÁN (las posiciones tienen su Stop Loss). "
                         "Gestiona el riesgo aprobando menos entradas, no cerrando lo abierto.")
        if snapshot.get("daily_pnl_pct") is not None:
            lines.append(f"P/L del día: {snapshot['daily_pnl_pct']:+.2%}")
        if snapshot.get("in_cooldown"):
            lines.append("ATENCIÓN: cooldown por pérdida diaria activo (no abrir entradas nuevas).")

        perf_by_symbol = {a["symbol"]: a for a in agents_overview.get("agents", [])}

        lines.append("\n=== SÍMBOLOS (señal del especialista + exposición) ===")
        for sym, sig in signals.items():
            si = snapshot.get("symbols", {}).get(sym, {})
            lines.append(f"\n[{sym}]")
            lines.append(f"  Señal: {sig.get('action')} | conf {self._pct(sig.get('confidence'))} | "
                         f"trend {sig.get('trend')} | riesgo {sig.get('risk_level')}")
            if sig.get("entry"):
                lines.append(f"  Niveles: entry {sig.get('entry')} SL {sig.get('stop_loss')} "
                             f"TP {sig.get('take_profit')}")
            reason = str(sig.get("reason", ""))[:200]
            if reason:
                lines.append(f"  Razón especialista: {reason}")
            open_pos = si.get("open_positions", 0) or 0
            pos_label = f"{open_pos}/{max_pos} pos" if max_pos > 0 else f"{open_pos} pos"
            at_max_tag = " ⛔ EN SU MÁXIMO (no abrir más)" if (max_pos > 0 and open_pos >= max_pos) else ""
            lines.append(f"  Exposición actual: {self._pct(si.get('exposure_pct'))} "
                         f"({pos_label}, "
                         f"P/L flotante {si.get('floating_pnl', 0):+.2f}){at_max_tag} | "
                         f"margen para asignar: {self._pct(si.get('remaining_pct'))}")
            # Spread actual vs filtro base: el director decide si fija un max_spread.
            max_sf = si.get("max_spread_filter") or 0.0
            cur_sp = si.get("current_spread")
            if max_sf > 0 or cur_sp is not None:
                cur_txt = f"{cur_sp:.1f}" if cur_sp is not None else "n/a"
                max_txt = f"{max_sf:.1f}" if max_sf > 0 else "sin filtro"
                wide = (" ⚠ ENSANCHADO" if (cur_sp is not None and max_sf > 0
                                            and cur_sp > max_sf) else "")
                lines.append(f"  Spread: actual {cur_txt} pts / filtro máx {max_txt} pts{wide}")
            # Antigüedad de las posiciones abiertas: la mesa avisa de las recién
            # abiertas (en gracia) para no cerrarlas antes de que evolucionen.
            if si.get("open_positions") and si.get("newest_position_age") is not None:
                min_hold = self.risk_book.min_hold_seconds
                age = si["newest_position_age"]
                grace = " ⏳ EN PERÍODO DE GRACIA (no cerrar aún salvo emergencia)" \
                    if (min_hold > 0 and age < min_hold) else ""
                oldest = si.get("oldest_position_age")
                rango = (f"reciente {self._fmt_age(age)}"
                         + (f", más antigua {self._fmt_age(oldest)}"
                            if oldest is not None and oldest != age else ""))
                lines.append(f"  Antigüedad posiciones: {rango}{grace}")
            nd = si.get("net_direction", "FLAT")
            long_pos = si.get("long_positions", 0) or 0
            short_pos = si.get("short_positions", 0) or 0
            lines.append(f"  Sesgo abierto: {long_pos}L / "
                         f"{short_pos}S · neto {nd} "
                         f"({self._pct(si.get('net_exposure_pct'))})")
            # P/L por lado: solo informativo en libro CUBIERTO (largos y cortos a la
            # vez); con un solo lado el P/L flotante total ya coincide con ese lado.
            if long_pos and short_pos:
                lines.append(f"  P/L por lado: largos {si.get('long_pnl', 0):+.2f} / "
                             f"cortos {si.get('short_pnl', 0):+.2f} "
                             f"(neto {si.get('floating_pnl', 0):+.2f})")
            trend_dir = RiskBook._trend_dir(sig.get("trend"))
            if nd in ("LONG", "SHORT") and trend_dir and trend_dir != nd:
                lines.append(f"  ⚠ CONFLICTO: libro {nd} vs tendencia {sig.get('trend')} "
                             f"-> considera reduce/close/hedge del lado {nd}.")
            a = perf_by_symbol.get(sym)
            p = (a or {}).get("performance") or {}
            if p.get("samples"):
                lines.append(f"  Rendimiento agente: win {self._pct(p.get('win_rate'))} "
                             f"sobre {p.get('samples', 0)} señales evaluadas")
            else:
                lines.append("  Rendimiento agente: sin histórico evaluado aún "
                             "(señales todavía sin resultado)")

        if news_context:
            lines.append(f"\n=== NOTICIAS / MACRO ===\n{news_context[:1500]}")

        lines.append("\nDecide la asignación de cartera. Responde solo el JSON.")
        return "\n".join(lines)

    @staticmethod
    def _pct(value) -> str:
        try:
            return f"{float(value):.0%}"
        except (TypeError, ValueError):
            return "n/a"

    @staticmethod
    def _fmt_age(seconds) -> str:
        """Antigüedad legible (s / m / h) para el contexto del coordinador."""
        try:
            s = float(seconds)
        except (TypeError, ValueError):
            return "n/a"
        if s < 90:
            return f"{s:.0f}s"
        if s < 5400:
            return f"{s / 60:.0f}m"
        return f"{s / 3600:.1f}h"

    # ----- Parseo / fallback -----

    @staticmethod
    def _to_float(value, default: float = 0.0) -> float:
        try:
            return float(value)
        except (TypeError, ValueError):
            return default

    @staticmethod
    def _to_int(value, default: int = 99) -> int:
        try:
            return int(value)
        except (TypeError, ValueError):
            return default

    def _parse(self, raw: str):
        """Extrae ``(rationale, decisions)`` del texto del LLM, o None si falla."""
        start = raw.find("{")
        end = raw.rfind("}") + 1
        if start == -1 or end == 0:
            return None
        try:
            data = json.loads(raw[start:end])
        except json.JSONDecodeError:
            return None
        decisions_raw = data.get("decisions")
        if not isinstance(decisions_raw, list):
            return None
        decisions = []
        for d in decisions_raw:
            if not isinstance(d, dict) or not d.get("symbol"):
                continue
            decisions.append({
                "symbol": str(d["symbol"]),
                "approve": bool(d.get("approve", False)),
                "priority": self._to_int(d.get("priority"), 99),
                "allocation_pct": self._to_float(d.get("allocation_pct"), 0.0),
                "position_action": str(d.get("position_action", "hold") or "hold").lower(),
                # R:R objetivo opcional para recortar/ampliar el TP del especialista.
                # 0/ausente => no ajustar (se respeta el TP del especialista).
                "tp_rr": self._to_float(d.get("tp_rr"), 0.0),
                # Multiplicador de lote opcional sobre el lote base del especialista.
                # 0/ausente => lote base (×1).
                "size_mult": self._to_float(d.get("size_mult"), 0.0),
                # Filtro de spread opcional (puntos) para ESTA entrada. 0/ausente =>
                # baseline configurado del especialista (transitorio, no lo reescribe).
                "max_spread": self._to_float(d.get("max_spread"), 0.0),
                "reason": str(d.get("reason", "")),
            })
        return str(data.get("rationale", "")), decisions

    def _fallback(self, signals: dict) -> list:
        """Decisión determinista cuando no hay LLM: aprueba las señales
        accionables con reparto igual del capital (acotado por el tope)."""
        actionable = [s for s in signals.values()
                      if str(s.get("action", "")).upper() in ("BUY", "SELL")]
        n = len(actionable) or 1
        alloc = min(self.risk_book.max_symbol_allocation_pct, 1.0 / n)
        decisions = []
        for i, sig in enumerate(actionable, 1):
            decisions.append({
                "symbol": sig.get("symbol"),
                "approve": True,
                "priority": i,
                "allocation_pct": round(alloc, 4),
                "position_action": "hold",
                "reason": "aprobada por defecto (coordinador LLM no disponible)",
            })
        return decisions
