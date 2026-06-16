import os
import json
import math
import logging
from typing import Optional
from core.models import BotConfig

import ollama

# El SDK de Gemini loguea "AFC is enabled..." (Automatic Function Calling) en
# cada llamada. No usamos function calling, así que silenciamos ese ruido.
logging.getLogger("google_genai.models").setLevel(logging.WARNING)
# httpx/httpcore loguean cada "HTTP Request: POST ... 200 OK" en INFO, lo que
# pisa el spinner de "Generando análisis". Los silenciamos a WARNING.
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)


def _call_openai(model: str, system: str, user: str, temperature: float = 0.2) -> Optional[str]:
    from openai import OpenAI
    client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
    response = client.chat.completions.create(
        model=model,
        messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
        temperature=temperature,
        response_format={"type": "json_object"},
    )
    return response.choices[0].message.content


def _call_deepseek(model: str, system: str, user: str, temperature: float = 0.2) -> Optional[str]:
    # DeepSeek expone una API compatible con OpenAI: reutilizamos el SDK de OpenAI
    # apuntando a su base_url y su clave (DEEPSEEK_API_KEY).
    from openai import OpenAI
    client = OpenAI(api_key=os.getenv("DEEPSEEK_API_KEY"),
                    base_url=os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com"))
    kwargs = {
        "model": model,
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
        "temperature": temperature,
    }
    # deepseek-chat soporta JSON mode; deepseek-reasoner (R1) NO acepta
    # response_format ni temperature, así que solo forzamos JSON fuera del reasoner
    # (el parseo de generate_signal extrae el JSON aunque venga con texto alrededor).
    if "reasoner" not in model.lower():
        kwargs["response_format"] = {"type": "json_object"}
    response = client.chat.completions.create(**kwargs)
    return response.choices[0].message.content


def _call_gemini(model: str, system: str, user: str, temperature: float = 0.2) -> Optional[str]:
    # SDK nuevo `google-genai` (el antiguo `google.generativeai` está deprecado).
    from google import genai
    from google.genai import types
    client = genai.Client(api_key=os.getenv("GEMINI_API_KEY"))
    response = client.models.generate_content(
        model=model,
        contents=user,
        config=types.GenerateContentConfig(
            system_instruction=system,
            temperature=temperature,
            response_mime_type="application/json",  # fuerza JSON, como openai/ollama
        ),
    )
    return response.text


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
- IDIOMA: redacta SIEMPRE el campo "rationale" en español (castellano), nunca en inglés.

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
  "rationale": "explicación breve EN ESPAÑOL citando los indicadores que justifican la decisión"
}
confidence es un decimal entre 0.0 y 1.0."""


class StrategyEngine:

    MIN_CONFIDENCE = 0.6
    MIN_RR = 1.0
    MAX_ENTRY_DEVIATION_PCT = 0.5  # entry no puede alejarse más de 0.5% del precio real

    def __init__(self, config: BotConfig, provider: str = "gemini",
                 system_suffix: str = "", min_confidence: float = None,
                 min_rr: float = None, temperature: float = 0.2,
                 commission_per_lot: float = None):
        self.config = config
        self.provider = provider.lower()
        # Persona/contexto adicional inyectado por un agente especializado.
        self.system_suffix = system_suffix
        # Umbrales por instancia: un agente puede ser más o menos conservador
        # que el resto sin tocar la clase global.
        self.min_confidence = self.MIN_CONFIDENCE if min_confidence is None else min_confidence
        self.min_rr = self.MIN_RR if min_rr is None else min_rr
        self.temperature = temperature
        # Directiva de estilo (perfil de riesgo + horizonte) inyectada en el prompt
        # para que el LLM cambie REALMENTE su disposición. La fija el orquestador
        # (_refresh_trading_directives) según el perfil activo; vacía = sin sesgo.
        self.trading_directive = ""
        # Apetito de riesgo: si el perfil activo es de apetito alto
        # (aggressive/extreme) se permite abrir señales risk_level="high" aunque
        # ya haya posiciones abiertas. Lo fija el orquestador según el perfil
        # (_refresh_trading_directives); el resto de guardarraíles siguen.
        self.allow_high_risk_with_positions = False
        # Comisión: puede venir del parámetro o del config (defaulteado a 7.0).
        self.commission_per_lot = commission_per_lot if commission_per_lot is not None else config.commission_per_lot
        if self.provider == "ollama":
            self._ollama = ollama.Client()

    def _call_ai(self, system: str, user: str) -> Optional[str]:
        model = self.config.model
        try:
            if self.provider == "openai":
                return _call_openai(model, system, user, self.temperature)
            if self.provider == "deepseek":
                return _call_deepseek(model, system, user, self.temperature)
            if self.provider == "gemini":
                return _call_gemini(model, system, user, self.temperature)
            response = self._ollama.chat(
                model=model,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                format="json",
                options={"temperature": self.temperature, "top_p": 0.9},
            )
            return response["message"]["content"]
        except Exception as e:
            if self.config.debug_mode:
                print(f"  [AI error] {self.provider}/{model}: {e}")
            return None

    def chat_json(self, system: str, user: str) -> Optional[str]:
        """Llamada LLM genérica que devuelve el texto crudo (se espera JSON).

        Reutiliza el transporte/proveedor de la estrategia (ollama/openai/gemini)
        y su manejo de errores para que otros agentes —p. ej. el coordinador—
        no dupliquen la fontanería. Devuelve el contenido o None si falla."""
        return self._call_ai(system, user)

    def analyze_market(self, symbol: str, timeframe: str = "H1", market_data: str = "") -> Optional[str]:
        user_prompt = f"""Analiza {symbol} (timeframe principal {timeframe}) y decide buy, sell o hold.

{market_data if market_data else 'No hay datos disponibles.'}

Devuelve solo el JSON con el formato especificado."""
        system = SYSTEM_PROMPT
        if self.system_suffix:
            system = f"{system}\n\n--- Especialización del agente ---\n{self.system_suffix}"
        if self.trading_directive:
            system = f"{system}\n\n--- Estilo de operación (perfil activo) ---\n{self.trading_directive}"
        return self._call_ai(system, user_prompt)

    @staticmethod
    def _to_float(value, default: float = 0.0) -> float:
        try:
            return float(value)
        except (TypeError, ValueError):
            return default

    def generate_signal(self, symbol: str, market_data: str = "") -> Optional[dict]:
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

    def validate_trade(self, signal: dict, positions: list = None, tick=None,
                       spread_points: float = None, total_open_positions: int = None,
                       enforce_max_positions: bool = True, min_rr: float = None) -> bool:
        """Valida señal antes de ejecutar. Devuelve False con el motivo impreso en debug.

        `positions` son las del símbolo (para no duplicar dirección). `spread_points`
        es el spread actual en puntos (filtro de coste). `total_open_positions` es el
        nº de posiciones de TODA la cuenta (límite global); si no se pasa, se usa el
        recuento del símbolo como aproximación.

        `enforce_max_positions`: si False, NO se aplica el límite global de número de
        posiciones (`max_open_positions`). Lo usa la ruta coordinada para delegar esa
        decisión a la mesa (que ya gobierna la exposición real vía RiskBook): así la
        mesa puede abrir más posiciones si lo considera necesario.

        `min_rr`: umbral de R:R a exigir en ESTA validación. Si es None se usa el de
        la instancia (`self.min_rr`). La ruta coordinada lo pasa cuando la mesa fija
        un R:R objetivo (tp_rr) más corto que el del especialista: el TP lo decide
        deliberadamente la mesa, así que la entrada se valida contra ese R:R."""
        effective_min_rr = self.min_rr if min_rr is None else min_rr
        def reject(reason: str) -> bool:
            if self.config.debug_mode:
                print(f"  [Validación] Rechazada: {reason}")
            return False

        action = signal["action"]
        if action == "HOLD":
            return False
        if action not in ("BUY", "SELL"):
            return reject(f"acción desconocida '{action}'")
        if signal["confidence"] < self.min_confidence:
            return reject(f"confianza {signal['confidence']:.0%} < {self.min_confidence:.0%}")
        if (signal.get("risk_level") == "high" and positions
                and not self.allow_high_risk_with_positions):
            return reject("riesgo alto con posiciones abiertas")

        # Una señal de confianza muy alta se salta el límite de posiciones del
        # símbolo (cuenta máxima y no-duplicar dirección) para poder reforzar.
        override = signal["confidence"] >= self.config.max_pos_override_confidence
        if override and self.config.debug_mode:
            print(f"  [Validación] Confianza {signal['confidence']:.0%} >= "
                  f"{self.config.max_pos_override_confidence:.0%}: se salta el límite de posiciones")

        open_count = total_open_positions if total_open_positions is not None else len(positions or [])
        if (enforce_max_positions and not override and self.config.max_open_positions
                and open_count >= self.config.max_open_positions):
            return reject(f"máximo de posiciones abiertas ({open_count}/{self.config.max_open_positions})")
        if (spread_points is not None and self.config.max_spread_filter
                and spread_points > self.config.max_spread_filter):
            return reject(f"spread {spread_points:.1f} pts > máximo {self.config.max_spread_filter:.1f}")

        # Nota: Permitimos múltiples posiciones en la misma dirección.
        # El límite total está controlado por max_open_positions.

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
            if risk and reward / risk < effective_min_rr:
                return reject(f"R:R 1:{reward / risk:.2f} por debajo del mínimo 1:{effective_min_rr}")

        return True

    def calculate_lot_size(self, account_balance: float, entry_price: float,
                           stop_loss_price: float, point: float, tick_value: float,
                           volume_min: float = 0.01, volume_max: float = 100.0,
                           volume_step: float = 0.01) -> float:
        """Lote que arriesga `risk_per_trade` del balance hasta el stop_loss.

        Función pura (recibe los datos del símbolo, no consulta al cliente) para
        poder testearla. Usa el valor real del tick del símbolo, así funciona
        igual para forex (contrato 100k) que para cripto (contrato 1):

            pérdida_por_lote = (distancia_al_SL / point) * tick_value
            lote = (balance * risk_per_trade) / pérdida_por_lote

        El resultado se redondea hacia abajo al `volume_step` (para no exceder el
        riesgo) y se acota a [volume_min, volume_max]. Si el mínimo del bróker ya
        arriesga más que el objetivo, devuelve ese mínimo."""
        risk_amount = account_balance * self.config.risk_per_trade
        price_diff = abs(entry_price - stop_loss_price)
        if price_diff <= 0 or point <= 0 or tick_value <= 0 or risk_amount <= 0:
            return volume_min
        loss_per_lot = (price_diff / point) * tick_value
        if loss_per_lot <= 0:
            return volume_min

        raw = risk_amount / loss_per_lot
        step = volume_step if volume_step > 0 else 0.01
        steps = math.floor(raw / step + 1e-9)
        lot = round(steps * step, 10)
        return max(volume_min, min(lot, volume_max))
