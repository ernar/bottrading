"""Orquestador de agentes.

De momento coordina la ejecución: en cada ciclo recorre los agentes activos,
les pide su análisis y ejecuta las señales válidas. Mantiene además un registro
de rendimiento por agente que servirá de base para la fase de optimización
(ajuste automático de parámetros / modelo de cada agente).
"""
import time

from core.state import bot_state
from core.logger import log_trade
from core.trade_metrics import calc_trade_metrics
from agents.base_agent import AgentParams


# Límites de seguridad para que la optimización no deje a un agente en una
# configuración absurda. (min, max)
PARAM_BOUNDS = {
    "min_confidence": (0.50, 0.85),
    "min_rr": (1.0, 3.0),
    "atr_sl_mult": (1.0, 3.5),
    "atr_tp_mult": (1.5, 5.0),
}
MIN_SAMPLES_TO_TUNE = 5   # nº mínimo de señales evaluadas para ajustar


def _clamp(value: float, key: str) -> float:
    lo, hi = PARAM_BOUNDS[key]
    return round(min(max(value, lo), hi), 2)


def tune_params(params: AgentParams, perf: dict, hold_rate: float) -> tuple:
    """Deriva nuevos parámetros a partir del rendimiento observado.

    Reglas explicables (no ML):
    - Win rate bajo  -> más selectivo (sube confianza mínima y R:R).
    - Win rate alto pero demasiados HOLD -> afloja confianza para capturar más.
    - Muchos SL tocados -> stops barridos por ruido: amplía SL y TP (mantiene R:R).
    - Casi ningún TP alcanzado con win rate decente -> objetivos demasiado
      lejanos: acerca el TP.

    Devuelve (nuevos_params, [lista de cambios legibles]).
    """
    if perf["samples"] < MIN_SAMPLES_TO_TUNE:
        return params, [f"datos insuficientes ({perf['samples']}/{MIN_SAMPLES_TO_TUNE} señales)"]

    min_conf = params.min_confidence
    min_rr = params.min_rr
    atr_sl = params.atr_sl_mult
    atr_tp = params.atr_tp_mult
    reasons = []

    if perf["win_rate"] < 0.40:
        min_conf += 0.05
        min_rr += 0.10
        reasons.append(f"win rate {perf['win_rate']:.0%} bajo -> +selectivo")
    elif perf["win_rate"] >= 0.65 and hold_rate > 0.60:
        min_conf -= 0.05
        reasons.append(f"win rate {perf['win_rate']:.0%} alto y {hold_rate:.0%} holds -> capturar más")

    if perf["sl_hit_rate"] > 0.40:
        atr_sl += 0.30
        atr_tp += 0.30
        reasons.append(f"{perf['sl_hit_rate']:.0%} SL tocados -> ampliar stops")

    if perf["tp_hit_rate"] < 0.15 and perf["win_rate"] >= 0.50:
        atr_tp -= 0.30
        reasons.append(f"solo {perf['tp_hit_rate']:.0%} TP alcanzados -> acercar objetivo")

    new_params = params.model_copy(update={
        "min_confidence": _clamp(min_conf, "min_confidence"),
        "min_rr": _clamp(min_rr, "min_rr"),
        "atr_sl_mult": _clamp(atr_sl, "atr_sl_mult"),
        "atr_tp_mult": _clamp(atr_tp, "atr_tp_mult"),
    })
    return new_params, (reasons or ["rendimiento dentro de rango, sin cambios"])


def _diff_params(old: AgentParams, new: AgentParams) -> list:
    """Lista legible de los campos que cambiaron."""
    changes = []
    for key in ("min_confidence", "min_rr", "atr_sl_mult", "atr_tp_mult"):
        o, n = getattr(old, key), getattr(new, key)
        if o != n:
            changes.append(f"{key}: {o} -> {n}")
    return changes


def _show_loading(message: str):
    for i in range(4):
        time.sleep(0.5)
        print(f"\r{message} {'.' * i}", end="", flush=True)
    print()


class AgentOrchestrator:

    def __init__(self, agents: list, client, platform: str = "mt5",
                 optimize_every_cycles: int = 0):
        self.agents = agents
        self.client = client
        self.platform = platform
        # Cada cuántos ciclos auto-optimizar (0 = desactivado).
        self.optimize_every_cycles = optimize_every_cycles
        # Contadores por agente: base para optimizar.
        self.stats = {a.name: {"signals": 0, "trades": 0, "holds": 0} for a in agents}
        # Reporte de la última optimización (para exponer al dashboard).
        self.last_optimization = None
        self.last_optimization_at = None

    # ----- Ejecución -----

    def run_forever(self, poll_seconds: int = 60):
        bot_state.set_bot_running(True)
        cycle = 0
        try:
            while True:
                account_info = self.client.get_account_info()
                if account_info:
                    bot_state.update_account(account_info)

                if not bot_state.bot_running:
                    time.sleep(5)
                    continue

                for agent in self.agents:
                    self._run_agent(agent)

                cycle += 1
                if self.optimize_every_cycles and cycle % self.optimize_every_cycles == 0:
                    self.optimize()

                time.sleep(poll_seconds)
        except KeyboardInterrupt:
            print("\n\nOrquestador detenido por el usuario.")

    def _run_agent(self, agent):
        symbol = agent.symbol
        print(f"\n{'=' * 50}")
        print(f"  [{agent.name}] Analizando {symbol}...")

        tick = self.client.get_tick(symbol)
        if tick:
            print(f"  Precio: Ask={tick.ask} | Bid={tick.bid}")

        _show_loading("  Generando análisis")
        signal = agent.analyze(self.client, platform=self.platform)
        if not signal:
            print("  No se generó señal.")
            return

        self.stats[agent.name]["signals"] += 1
        bot_state.update_signal(signal)

        print(f"\n  Señal: {signal['action']} | Confianza: {signal['confidence']:.0%}")
        print(f"  Tendencia: {signal.get('trend', 'N/A')} | Riesgo: {signal.get('risk_level', 'N/A')}")
        if signal.get("entry"):
            print(f"  Entry: {signal['entry']} | SL: {signal['stop_loss']} | TP: {signal['take_profit']}")
            metrics = calc_trade_metrics(
                self.client, symbol, signal["action"],
                signal["entry"], signal["stop_loss"], signal["take_profit"],
                agent.params.lot_size,
            )
            if metrics:
                print(f"  Profit potencial: +${metrics['net_profit']:.2f}  ({metrics['pips_tp']:.0f} pips)")
                print(f"  Pérdida potencial: -${metrics['net_loss']:.2f}  ({metrics['pips_sl']:.0f} pips)")
                print(f"  Comisión estimada: ${metrics['commission']:.2f} | R:R = 1:{metrics['rr']}")
        print(f"  Razón: {signal['reason']}")

        positions = self.client.get_positions(symbol)
        for position in positions:
            bot_state.update_position(symbol, position)

        if signal["action"] == "HOLD":
            self.stats[agent.name]["holds"] += 1
            return

        if not agent.validate(signal, positions, tick=self.client.get_tick(symbol)):
            print("  Señal no validada para ejecución.")
            return

        result = self.client.place_order(
            symbol=symbol,
            volume=agent.params.lot_size,
            order_type=signal["action"],
            stop_loss=signal.get("stop_loss") or None,
            take_profit=signal.get("take_profit") or None,
            comment=f"{agent.name}: {signal['reason'][:18]}",
        )
        if result and result.get("success"):
            print(f"  Orden ejecutada: ticket {result.get('order')} @ {result.get('price')}")
            self.stats[agent.name]["trades"] += 1
            log_trade(
                symbol=symbol,
                action=signal["action"],
                volume=agent.params.lot_size,
                price=result.get("price") or signal.get("entry", 0),
                stop_loss=signal.get("stop_loss", 0),
                take_profit=signal.get("take_profit", 0),
                result=result,
                platform=self.platform,
            )
        elif result and result.get("timeout"):
            print("  [!] TIMEOUT esperando al EA: la orden NO se confirmó.")
            print("      La orden PUEDE haberse ejecutado igualmente. Revisa MT4")
            print("      antes de que el orquestador reintente en el próximo ciclo.")
        else:
            err = (result or {}).get("error") or (result or {}).get("comment") or "sin respuesta"
            print(f"  Error al ejecutar orden: {err}")

    # ----- Optimización -----

    def optimize(self, apply: bool = True) -> list:
        """Ajusta los parámetros de cada agente según su rendimiento real.

        Lee la memoria de señales evaluadas de cada agente, deriva nuevos
        parámetros con reglas explicables (ver tune_params) y, si apply=True,
        los aplica en caliente. Devuelve un reporte por agente.

        Pasa apply=False para una simulación (dry-run) sin modificar nada.
        """
        print(f"\n{'=' * 50}")
        print(f"  OPTIMIZACIÓN DE AGENTES{'  (dry-run)' if not apply else ''}")
        print("=" * 50)

        report = []
        for agent in self.agents:
            perf = agent.memory.get_performance(agent.symbol)
            stats = self.stats[agent.name]
            hold_rate = stats["holds"] / stats["signals"] if stats["signals"] else 0.0

            new_params, reasons = tune_params(agent.params, perf, hold_rate)
            changes = _diff_params(agent.params, new_params)

            print(f"\n  [{agent.name}] {agent.symbol}")
            print(f"    Rendimiento: {perf['samples']} señales | win {perf['win_rate']:.0%} | "
                  f"SL {perf['sl_hit_rate']:.0%} | TP {perf['tp_hit_rate']:.0%} | "
                  f"mov medio {perf['avg_move_pct']:+.2f}% | holds {hold_rate:.0%}")
            print(f"    Diagnóstico: {'; '.join(reasons)}")
            if changes:
                print(f"    Cambios: {', '.join(changes)}")
                if apply:
                    agent.apply_params(new_params)
            else:
                print("    Sin cambios.")

            report.append({
                "agent": agent.name,
                "symbol": agent.symbol,
                "performance": perf,
                "hold_rate": round(hold_rate, 3),
                "reasons": reasons,
                "changes": changes,
                "applied": bool(changes) and apply,
            })

        print("\n" + "=" * 50)
        if apply:
            self.last_optimization = report
            self.last_optimization_at = time.strftime("%Y-%m-%d %H:%M:%S")
        return report

    # ----- Exposición para el dashboard -----

    def agents_overview(self) -> dict:
        """Resumen de cada agente (config + stats de sesión + rendimiento de
        memoria) y la última optimización aplicada. Lo consume /api/agents."""
        agents = []
        for agent in self.agents:
            p = agent.params
            agents.append({
                "name": agent.name,
                "symbol": agent.symbol,
                "description": agent.description,
                "provider": p.provider,
                "model": p.model,
                "params": {
                    "min_confidence": p.min_confidence,
                    "min_rr": p.min_rr,
                    "atr_sl_mult": p.atr_sl_mult,
                    "atr_tp_mult": p.atr_tp_mult,
                    "lot_size": p.lot_size,
                },
                "stats": self.stats[agent.name],
                "performance": agent.memory.get_performance(agent.symbol),
            })
        return {
            "agents": agents,
            "optimize_every_cycles": self.optimize_every_cycles,
            "last_optimization": self.last_optimization,
            "last_optimization_at": self.last_optimization_at,
        }
