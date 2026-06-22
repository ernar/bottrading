"""Vuelca el histórico de velas H1 de los símbolos a un JSON para backtesting OFFLINE.

Conecta al bridge MT4 (EA PythonBridge adjunto y AutoTrading ON), pide N velas H1
por símbolo y su info (point/digits/tick_value/lotes/spread/comisión), ALINEA las
series por timestamp (intersección de tiempos comunes a todos los símbolos, para
que el cliente de replay las case bien por índice) y escribe el formato multi-símbolo
que consume ``python -m core.backtest --mode coordinated --candles <archivo>``:

    {"series": {"BTCUSD": [{time,open,high,low,close,volume}, ...], ...},
     "infos":  {"BTCUSD": {point, digits, contract_size, spread_points,
                           commission_per_lot, tick_value, volume_min, volume_step}, ...}}

Uso:
    python scripts/dump_history.py --symbols BTCUSD,ETHUSD --bars 3000 --out logs/history.json
    python scripts/dump_history.py --agents btc-agent,eth-agent --commission 7
Sin --symbols ni --agents usa los símbolos de TODOS los blueprints del registro.

Nota: la alineación por timestamp es fiable para símbolos de la MISMA clase (p. ej.
cripto BTC/ETH, 24/7). Mezclar clases con sesiones distintas (forex/índices) deja
menos velas comunes; el backtest sigue siendo válido sobre esa intersección.
"""
import argparse
import json
import os
import sys

# Permite ejecutar el script directamente (python scripts/dump_history.py).
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def align_by_time(series: dict) -> dict:
    """Alinea las series por timestamp: con >1 símbolo conserva solo las velas cuyo
    ``time`` exista en TODOS (intersección), ordenadas. Así el cliente de replay
    (que avanza por índice) tiene la misma rejilla temporal en todos los símbolos."""
    if not series:
        return {}
    if len(series) == 1:
        s, cs = next(iter(series.items()))
        return {s: sorted(cs, key=lambda c: c["time"])}
    common = set.intersection(*[{c["time"] for c in cs} for cs in series.values()])
    return {s: sorted([c for c in cs if c["time"] in common], key=lambda c: c["time"])
            for s, cs in series.items()}


def _symbols(args) -> list:
    from agents.registry import AGENT_BLUEPRINTS, build_agent
    if args.symbols:
        return [s.strip() for s in args.symbols.split(",") if s.strip()]
    if args.agents:
        return [build_agent(n.strip()).symbol for n in args.agents.split(",") if n.strip()]
    return [bp.symbol for bp in AGENT_BLUEPRINTS]


def _info(mt, symbol, commission):
    info = mt.get_symbol_info(symbol)
    point = float(getattr(info, "point", 0.00001) or 0.00001) if info else 0.00001
    spread_pts = float(getattr(info, "spread", 0.0) or 0.0) if info else 0.0
    if not spread_pts:
        tick = mt.get_tick(symbol)
        if tick and point:
            spread_pts = round((tick.ask - tick.bid) / point, 1)
    comm = commission
    if comm is None:
        comm = mt.get_commission_per_lot(symbol) or 0.0
    g = lambda a, d: (getattr(info, a, d) if info else d)
    return {
        "point": point,
        "digits": int(g("digits", 2)),
        # MT4 no reporta contract_size por SYMBOL_INFO -> 1.0 (igual que asume el bot
        # en vivo, vía _contract_size). Edita el JSON si tu símbolo difiere.
        "contract_size": float(g("trade_contract_size", g("contract_size", 1.0)) or 1.0),
        "spread_points": float(spread_pts or 0.0),
        "commission_per_lot": float(comm or 0.0),
        "tick_value": float(g("trade_tick_value", 1.0) or 1.0),
        "volume_min": float(g("volume_min", 0.01) or 0.01),
        "volume_step": float(g("volume_step", 0.01) or 0.01),
    }


def main(argv=None):
    ap = argparse.ArgumentParser(description="Vuelca histórico H1 a JSON para backtesting.")
    ap.add_argument("--symbols", default="", help="coma; p.ej. BTCUSD,ETHUSD")
    ap.add_argument("--agents", default="", help="coma; nombres de agentes del registro")
    ap.add_argument("--bars", type=int, default=3000)
    ap.add_argument("--commission", type=float, default=None,
                    help="comisión por lote (ida+vuelta). Por defecto la aprendida del bróker o 0.")
    ap.add_argument("--out", default=os.path.join("logs", "history.json"))
    args = ap.parse_args(argv)

    from clients.mt4_client import MT4Client
    mt = MT4Client()
    if not mt.connect():
        print("No se pudo conectar al bridge MT4. ¿EA PythonBridge adjunto y AutoTrading ON?")
        return 1

    symbols = _symbols(args)
    series, infos = {}, {}
    for sym in symbols:
        candles = mt.get_ohlcv(sym, "H1", args.bars)
        if not candles:
            print(f"  ! {sym}: sin velas (¿símbolo en Observación de Mercado?), se omite.")
            continue
        series[sym] = candles
        infos[sym] = _info(mt, sym, args.commission)
        print(f"  + {sym}: {len(candles)} velas H1 · spread {infos[sym]['spread_points']} pts "
              f"· comisión {infos[sym]['commission_per_lot']}/lote")

    if not series:
        print("No se volcó ningún símbolo.")
        return 1

    series = align_by_time(series)
    n = min(len(c) for c in series.values())
    if n == 0:
        print("Tras alinear por timestamp no quedan velas comunes a todos los símbolos. "
              "Vuelca símbolos de la misma clase (p. ej. solo cripto) o por separado.")
        return 1

    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as fh:
        json.dump({"series": series, "infos": infos}, fh)

    engines = (",".join(s.strip() for s in args.agents.split(",")) if args.agents
               else "btc-agent,eth-agent")
    print(f"\nGuardado {args.out} · {len(series)} símbolos · {n} velas comunes (alineadas por timestamp).")
    print("Corre el backtest coordinado (mesa determinista, sin coste de LLM):\n"
          f"  python -m core.backtest --mode coordinated --candles {args.out} "
          f"--engines {engines} --signal baseline --coord deterministic")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
