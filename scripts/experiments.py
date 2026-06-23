"""Runner de EXPERIMENTOS: barre varias configuraciones de backtest coordinado y
saca una tabla RANKEADA de una sola vez, para no ir probando a mano.

Cada combinación = producto cartesiano de:
  - conjunto de agentes (--symbol-sets, ';' separa subconjuntos),
  - motor de señal (--signal: baseline y/o llm),
  - override de min_confidence (--min-conf), min_rr (--min-rr) y atr_tp_mult (--tp-mult).

Reutiliza el motor coordinado real (mesa + ejecutor) vía core.backtest.run_one.
Escribe en una DB temporal (no toca producción).

Ejemplos:
  # ¿qué conjunto de símbolos rinde mejor? (base determinista, gratis)
  python scripts/experiments.py --candles logs/hist_cripto.json \
      --engines btc-agent,eth-agent --symbol-sets "btc-agent,eth-agent;eth-agent;btc-agent"

  # barrido de R:R y selectividad sobre cripto
  python scripts/experiments.py --candles logs/hist_cripto.json --engines btc-agent,eth-agent \
      --tp-mult 2,3,4 --min-rr 1.3,2.0 --rank net

  # ¿aporta el LLM frente a la base? (cuidado: --signal llm gasta LLM)
  python scripts/experiments.py --candles logs/hist_cripto.json --engines btc-agent,eth-agent \
      --signal baseline,llm
"""
import argparse
import itertools
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _floats(s):
    return [float(x) for x in s.split(",") if x.strip()]


def _fmt(v, nd=2, pct=False, na="n/a"):
    if v is None:
        return na
    return f"{v * 100:.1f}%" if pct else f"{v:.{nd}f}"


def main(argv=None):
    ap = argparse.ArgumentParser(description="Barrido de configuraciones de backtest coordinado.")
    ap.add_argument("--candles", required=True, help="JSON de histórico (scripts/dump_history.py)")
    ap.add_argument("--engines", required=True, help="agentes disponibles (coma)")
    ap.add_argument("--symbol-sets", default="",
                    help="';' separa subconjuntos de agentes; vacío = un solo set = --engines")
    ap.add_argument("--signal", default="baseline", help="coma: baseline y/o llm")
    ap.add_argument("--coord", default="deterministic", choices=["deterministic", "llm"])
    ap.add_argument("--min-conf", default="", help="coma; override de min_confidence")
    ap.add_argument("--min-rr", default="", help="coma; override de min_rr")
    ap.add_argument("--tp-mult", default="", help="coma; override de atr_tp_mult (base y llm)")
    ap.add_argument("--warmup", type=int, default=120)
    ap.add_argument("--balance", type=float, default=1000.0)
    ap.add_argument("--rank", default="net",
                    choices=["expectancy", "pf", "net", "return", "trades"])
    ap.add_argument("--top", type=int, default=0, help="muestra solo las N mejores (0 = todas)")
    args = ap.parse_args(argv)

    from core import db
    from core.backtest import load_history, run_one

    db.init_db("sqlite:///" + os.path.join(tempfile.gettempdir(), "experiments.db"))
    series, infos = load_history(args.candles)

    if args.symbol_sets:
        sets = [[e.strip() for e in grp.split(",") if e.strip()]
                for grp in args.symbol_sets.split(";") if grp.strip()]
    else:
        sets = [[e.strip() for e in args.engines.split(",") if e.strip()]]
    signals = [s.strip() for s in args.signal.split(",") if s.strip()]
    confs = _floats(args.min_conf) or [None]
    rrs = _floats(args.min_rr) or [None]
    tps = _floats(args.tp_mult) or [None]

    combos = list(itertools.product(sets, signals, confs, rrs, tps))
    print(f"Corriendo {len(combos)} configuraciones...\n")
    rows = []
    for sset, sig, mc, rr, tp in combos:
        overrides = {}
        if mc is not None:
            overrides["min_confidence"] = mc
        if rr is not None:
            overrides["min_rr"] = rr
        if tp is not None:
            overrides["atr_tp_mult"] = tp
        try:
            res = run_one(series, infos, sset, signal=sig, coord=args.coord,
                          overrides=overrides, warmup=args.warmup, balance=args.balance)
        except Exception as e:  # un combo no debe tumbar el barrido entero
            res = {"ok": False, "error": str(e)[:60]}
        rows.append({"set": "+".join(s.replace("-agent", "") for s in sset),
                     "sig": sig, "conf": mc, "rr": rr, "tp": tp, "res": res})

    def rank_key(r):
        res = r["res"]
        if not res.get("ok"):
            return float("-inf")
        m = res.get("metrics", {})
        return {
            "expectancy": m.get("expectancy"),
            "pf": m.get("profit_factor"),
            "net": m.get("total_pnl"),
            "return": res.get("return_pct"),
            "trades": res.get("trades"),
        }.get(args.rank) or float("-inf")

    rows.sort(key=rank_key, reverse=True)
    if args.top:
        rows = rows[:args.top]

    hdr = (f"{'#':>2} {'símbolos':<16}{'señal':<9}{'conf':>6}{'rr':>5}{'tp':>5}"
           f"{'trades':>8}{'win':>8}{'PF':>7}{'expect':>8}{'P/L':>9}{'ret%':>8}{'maxDD':>7}")
    print(hdr)
    print("-" * len(hdr))
    for i, r in enumerate(rows, 1):
        res = r["res"]
        cfg = (f"{i:>2} {r['set'][:16]:<16}{r['sig']:<9}"
               f"{_fmt(r['conf'], 2):>6}{_fmt(r['rr'], 1):>5}{_fmt(r['tp'], 1):>5}")
        if not res.get("ok"):
            print(cfg + f"  ERROR: {res.get('error')}")
            continue
        m = res["metrics"]
        print(cfg + f"{res['trades']:>8}{_fmt(m['win_rate'], pct=True):>8}"
              f"{_fmt(m['profit_factor']):>7}{_fmt(m['expectancy'], 3):>8}"
              f"{_fmt(m['total_pnl']):>9}{_fmt(res['return_pct'], pct=True):>8}"
              f"{_fmt(res['max_drawdown'], pct=True):>7}")

    # Pista si la mejor fila no opera (config demasiado restrictiva o datos escasos).
    best = rows[0]["res"] if rows else {}
    if best.get("ok") and best.get("trades", 0) == 0:
        d = best.get("diagnostics", {})
        print(f"\n(La mejor config no operó: {d.get('candles')} velas, "
              f"{d.get('actionable_signals')} señales, {d.get('approved_by_mesa')} aprobadas. "
              "Baja --warmup, vuelca más velas o afloja umbrales.)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
