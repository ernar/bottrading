import os
import json
from typing import Optional
from core.models import BotConfig

import ollama


def _call_openai(model: str, system: str, user: str) -> Optional[str]:
    from openai import OpenAI
    client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
    response = client.chat.completions.create(
        model=model,
        messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
        temperature=0.2,
        response_format={"type": "json_object"},
    )
    return response.choices[0].message.content


def _call_gemini(model: str, system: str, user: str) -> Optional[str]:
    import google.generativeai as genai
    genai.configure(api_key=os.getenv("GEMINI_API_KEY"))
    gm = genai.GenerativeModel(model, system_instruction=system)
    return gm.generate_content(user).text


SYSTEM_PROMPT = """Eres un analista técnico de trading de corto plazo. Recibes datos de mercado \
ya procesados: precio actual, indicadores (RSI, EMA20/50, MACD, Bollinger, ATR), soportes y \
resistencias, velas recientes, posiciones abiertas, noticias/eventos económicos y el \
rendimiento de tus señales anteriores.

Reglas de análisis:
- Basa tu decisión EXCLUSIVAMENTE en los datos proporcionados. No inventes noticias ni datos que no estén en el contexto.
- Si hay un evento económico de impacto High a menos de 2 horas que afecte al símbolo, devuelve "hold": la volatilidad del dato puede barrer cualquier stop técnico.
- Usa los titulares solo como contexto de sentimiento; la decisión principal debe apoyarse en los indicadores técnicos.
- Busca confluencia: solo da buy/sell si al menos 2-3 indicadores apuntan en la misma dirección.
- Si la tendencia H4 contradice la señal H1, reduce la confianza o devuelve hold.
- Si RSI está en zona extrema (>70 o <30), evita entrar a favor del movimiento agotado.
- Aprende del rendimiento reciente: si tus últimas señales en este símbolo fallaron, sé más conservador.
- Si no hay ventaja clara, devuelve "hold" con confianza baja. Un hold correcto es mejor que un trade malo.

Reglas de niveles:
- entry: usa el precio actual (Ask para buy, Bid para sell).
- stop_loss: detrás del soporte/resistencia más cercano o 1.5×ATR del entry.
- take_profit: ratio riesgo/beneficio mínimo 1:1.5; apunta al siguiente nivel S/R.
- Para buy: stop_loss < entry < take_profit. Para sell: take_profit < entry < stop_loss.

Responde SOLO con JSON válido, sin texto adicional:
{
  "symbol": "EURUSD",
  "trend": "bullish|bearish|sideways",
  "action": "buy|sell|hold",
  "confidence": 0.0,
  "entry": 0.0,
  "stop_loss": 0.0,
  "take_profit": 0.0,
  "risk_level": "low|medium|high",
  "rationale": "explicación breve citando los indicadores que justifican la decisión"
}
confidence es un decimal entre 0.0 y 1.0."""


class StrategyEngine:

    MIN_CONFIDENCE = 0.6
    MIN_RR = 1.0
    MAX_ENTRY_DEVIATION_PCT = 0.5  # entry no puede alejarse más de 0.5% del precio real

    def __init__(self, config: BotConfig, provider: str = "ollama"):
        self.config = config
        self.provider = provider.lower()
        if self.provider == "ollama":
            self._ollama = ollama.Client()

    def _call_ai(self, system: str, user: str) -> Optional[str]:
        model = self.config.model
        try:
            if self.provider == "openai":
                return _call_openai(model, system, user)
            if self.provider == "gemini":
                return _call_gemini(model, system, user)
            response = self._ollama.chat(
                model=model,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                format="json",
                options={"temperature": 0.2, "top_p": 0.9},
            )
            return response["message"]["content"]
        except Exception as e:
            if self.config.debug_mode:
                print(f"  [AI error] {self.provider}/{model}: {e}")
            return None

    def analyze_market(self, symbol: str, timeframe: str = "H1", market_data: str = "") -> Optional[str]:
        user_prompt = f"""Analiza {symbol} (timeframe principal {timeframe}) y decide buy, sell o hold.

{market_data if market_data else 'No hay datos disponibles.'}

Devuelve solo el JSON con el formato especificado."""
        return self._call_ai(SYSTEM_PROMPT, user_prompt)

    @staticmethod
    def _to_float(value, default: float = 0.0) -> float:
        try:
            return float(value)
        except (TypeError, ValueError):
            return default

    def generate_signal(self, symbol: str, positions: list = None, market_data: str = "") -> Optional[dict]:
        analysis = self.analyze_market(symbol, market_data=market_data)
        if not analysis:
            return None

        start = analysis.find("{")
        end = analysis.rfind("}") + 1
        if start == -1 or end == 0:
            return None

        try:
            signal = json.loads(analysis[start:end])
        except json.JSONDecodeError:
            return None

        confidence = self._to_float(signal.get("confidence"))
        if confidence > 1:  # el modelo a veces responde en porcentaje (85 en vez de 0.85)
            confidence /= 100

        return {
            "symbol": signal.get("symbol", symbol),
            "trend": signal.get("trend", "sideways"),
            "action": str(signal.get("action", "hold")).upper(),
            "confidence": confidence,
            "entry": self._to_float(signal.get("entry")),
            "stop_loss": self._to_float(signal.get("stop_loss")),
            "take_profit": self._to_float(signal.get("take_profit")),
            "risk_level": signal.get("risk_level", "medium"),
            "reason": signal.get("rationale", "") or signal.get("reason", ""),
        }

    def validate_trade(self, signal: dict, positions: list = None, tick=None) -> bool:
        """Valida señal antes de ejecutar. Devuelve False con el motivo impreso en debug."""
        def reject(reason: str) -> bool:
            if self.config.debug_mode:
                print(f"  [Validación] Rechazada: {reason}")
            return False

        action = signal["action"]
        if action == "HOLD":
            return False
        if action not in ("BUY", "SELL"):
            return reject(f"acción desconocida '{action}'")
        if signal["confidence"] < self.MIN_CONFIDENCE:
            return reject(f"confianza {signal['confidence']:.0%} < {self.MIN_CONFIDENCE:.0%}")
        if signal.get("risk_level") == "high" and positions:
            return reject("riesgo alto con posiciones abiertas")
        if self.config.max_open_positions and len(positions or []) >= self.config.max_open_positions:
            return reject(f"máximo de posiciones abiertas ({self.config.max_open_positions})")

        # No duplicar posición en la misma dirección sobre el mismo símbolo
        for p in positions or []:
            direction = getattr(p, "direction", None) or (p.get("type") if isinstance(p, dict) else None)
            if direction and str(direction).upper() == action:
                return reject(f"ya existe posición {action} en este símbolo")

        entry = signal.get("entry") or 0
        sl = signal.get("stop_loss") or 0
        tp = signal.get("take_profit") or 0

        # Entry debe estar cerca del precio real de mercado
        if tick and entry:
            market_price = tick.ask if action == "BUY" else tick.bid
            if market_price and abs(entry - market_price) / market_price * 100 > self.MAX_ENTRY_DEVIATION_PCT:
                return reject(f"entry {entry} se aleja >{self.MAX_ENTRY_DEVIATION_PCT}% del precio real {market_price}")

        # Sanity de SL/TP por lado + R:R mínimo
        if entry and sl and tp:
            if action == "BUY" and not (sl < entry < tp):
                return reject(f"niveles incoherentes para BUY (SL={sl}, entry={entry}, TP={tp})")
            if action == "SELL" and not (tp < entry < sl):
                return reject(f"niveles incoherentes para SELL (TP={tp}, entry={entry}, SL={sl})")
            risk = abs(entry - sl)
            reward = abs(tp - entry)
            if risk and reward / risk < self.MIN_RR:
                return reject(f"R:R 1:{reward / risk:.2f} por debajo del mínimo 1:{self.MIN_RR}")

        return True

    def calculate_lot_size(self, account_balance: float, symbol: str,
                           stop_loss_price: float, entry_price: float) -> float:
        risk_amount = account_balance * self.config.risk_per_trade
        price_diff = abs(entry_price - stop_loss_price)
        if price_diff == 0:
            return self.config.default_lot_size
        lot_size = risk_amount / (price_diff * 100000)
        return max(self.config.default_lot_size, round(lot_size, 2))
