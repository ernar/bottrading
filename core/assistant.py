"""Asistente conversacional "Responsable de la organización".

Un chatbot que actúa como el director/responsable de la mesa de trading: el
usuario le pregunta en lenguaje natural y él responde consultando el estado en
vivo del bot (cuenta, posiciones, señales, decisiones de la mesa, rendimiento de
los agentes). NO ejecuta órdenes ni cambia configuración: solo informa y explica.

Diseño:
- **Memoria por sesión**: cada `session_id` conserva su historial de turnos. Para
  no inflar el contexto (ni el coste), el historial se acota a los últimos
  `MAX_TURNS` mensajes; cuando se supera, los más antiguos se condensan en un
  `summary` por sesión (un resumen rodante que se reinyecta como contexto).
- **Contexto en vivo**: en cada turno se construye un snapshot compacto del
  estado del bot (vía getters inyectados desde el API) y se antepone como
  contexto del sistema, para que las respuestas reflejen los datos del momento.
- **LLM**: Gemini por defecto (gemini-3.5-flash), usando la GEMINI_API_KEY
  configurada como secreto en Ajustes. Fail-safe: si el LLM no está disponible,
  responde con un mensaje claro en vez de romper.

Es deliberadamente independiente de `StrategyEngine` (que fuerza salida JSON):
aquí queremos texto conversacional multi-turno.
"""
import os
import time
from typing import Callable, Optional


def _gemini_chat(model: str, system: str, history: list, temperature: float = 0.4) -> str:
    """Llamada multi-turno a Gemini (SDK `google-genai`).

    `history` es una lista de ``{"role": "user"|"model", "content": str}``.
    Devuelve el texto de la respuesta. Lanza la excepción del SDK si falla
    (el llamador la captura)."""
    from google import genai
    from google.genai import types

    client = genai.Client(api_key=os.getenv("GEMINI_API_KEY"))
    contents = [
        types.Content(role=("model" if m["role"] == "model" else "user"),
                      parts=[types.Part(text=m["content"])])
        for m in history if m.get("content")
    ]
    response = client.models.generate_content(
        model=model,
        contents=contents,
        config=types.GenerateContentConfig(
            system_instruction=system,
            temperature=temperature,
        ),
    )
    return (response.text or "").strip()


def _openai_chat(model: str, system: str, history: list, temperature: float = 0.4) -> str:
    """Fallback OpenAI (texto conversacional)."""
    from openai import OpenAI
    client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
    messages = [{"role": "system", "content": system}]
    for m in history:
        role = "assistant" if m["role"] == "model" else "user"
        messages.append({"role": role, "content": m["content"]})
    resp = client.chat.completions.create(
        model=model, messages=messages, temperature=temperature)
    return (resp.choices[0].message.content or "").strip()


def _ollama_chat(model: str, system: str, history: list, temperature: float = 0.4) -> str:
    """Fallback Ollama local (texto conversacional)."""
    import ollama
    messages = [{"role": "system", "content": system}]
    for m in history:
        role = "assistant" if m["role"] == "model" else "user"
        messages.append({"role": role, "content": m["content"]})
    resp = ollama.Client().chat(
        model=model, messages=messages, options={"temperature": temperature})
    return (resp["message"]["content"] or "").strip()


SYSTEM_PROMPT = """Eres el RESPONSABLE de una mesa de trading algorítmico (una "empresa de bróker"). \
Hablas en primera persona como el director de la organización. El usuario es el dueño del bot y te \
hace preguntas sobre cómo va todo; tú respondes con criterio, de forma clara y honesta, apoyándote \
SIEMPRE en los datos en vivo que te paso en el bloque "ESTADO ACTUAL".

Cómo trabajas:
- Responde en español, en tono profesional pero cercano, como un gestor que rinde cuentas a su jefe.
- Básate EXCLUSIVAMENTE en los datos del ESTADO ACTUAL y en lo que ya se ha hablado en la conversación. \
NO inventes cifras, posiciones ni noticias. Si un dato no está, dilo con naturalidad ("ahora mismo no \
tengo ese dato") y, si procede, sugiere dónde mirarlo en el dashboard.
- Sé concreto: cita equity, P/L, exposición, posiciones, decisiones de la mesa, rendimiento de los \
agentes... con sus números. Redondea de forma legible.
- Si te preguntan "¿cómo vamos?", da un resumen ejecutivo: estado de la cuenta, riesgo/exposición, \
qué está haciendo la mesa y cualquier alerta (cooldown, hard-stop, conflicto de reversión).
- Explica el PORQUÉ de las decisiones de la mesa cuando te lo pregunten (apetito, topes de exposición, \
guardias de reversión, período de gracia, etc.).
- NO ejecutas órdenes ni cambias ajustes: si te lo piden, explica que tú solo informas y diriges la \
estrategia, y que esas acciones se hacen desde las pestañas correspondientes del dashboard.
- Sé breve por defecto; entra en detalle solo si te lo piden o si la situación lo requiere (p. ej. una \
alerta de riesgo). Usa listas o viñetas cuando ayuden a la claridad.
"""


class OrgAssistant:
    """Asistente conversacional con memoria por sesión y contexto en vivo."""

    # Nº máximo de mensajes (user+model) que se conservan literales por sesión.
    MAX_TURNS = 24
    # A partir de cuántos mensajes se condensa la mitad más antigua en summary.
    SUMMARY_THRESHOLD = 20

    def __init__(self, provider: str = "gemini", model: Optional[str] = None,
                 temperature: float = 0.4):
        self.provider = (provider or "gemini").lower()
        self.model = model or os.getenv("ASSISTANT_MODEL", "gemini-3.5-flash")
        self.temperature = temperature
        # Getters inyectados desde el API para construir el contexto en vivo.
        self._context_builder: Optional[Callable[[], str]] = None
        # {session_id: {"history": [...], "summary": str, "updated": ts}}
        self._sessions: dict = {}

    # ----- Configuración -----

    def set_context_builder(self, fn: Callable[[], str]) -> None:
        """Registra la función que devuelve el contexto en vivo (texto)."""
        self._context_builder = fn

    def configure(self, provider: str = None, model: str = None,
                  temperature: float = None) -> None:
        """Reconfigura el LLM en caliente (lo usa la recarga de ajustes)."""
        if provider:
            self.provider = provider.lower()
        if model:
            self.model = model
        if temperature is not None:
            self.temperature = temperature

    # ----- Memoria por sesión -----

    def _session(self, session_id: str) -> dict:
        return self._sessions.setdefault(
            session_id, {"history": [], "summary": "", "updated": time.time()})

    def history(self, session_id: str) -> list:
        """Historial visible de la sesión (lista de {role, content})."""
        return list(self._session(session_id)["history"])

    def reset(self, session_id: str) -> None:
        """Olvida la conversación de una sesión."""
        self._sessions.pop(session_id, None)

    def _maybe_summarize(self, sess: dict) -> None:
        """Condensa la mitad más antigua del historial en `summary` cuando crece
        demasiado, para mantener el contexto acotado (memoria a largo plazo)."""
        hist = sess["history"]
        if len(hist) < self.SUMMARY_THRESHOLD:
            return
        keep = self.MAX_TURNS // 2
        old, recent = hist[:-keep], hist[-keep:]
        if not old:
            return
        transcript = "\n".join(
            f"{'Usuario' if m['role'] == 'user' else 'Tú'}: {m['content']}" for m in old)
        prev = sess.get("summary", "")
        prompt = (
            "Resume de forma muy concisa los puntos clave de esta conversación "
            "para recordarlos más adelante (decisiones, dudas del usuario, temas "
            "tratados). Máximo 8 líneas.\n\n"
            + (f"Resumen previo:\n{prev}\n\n" if prev else "")
            + f"Conversación:\n{transcript}"
        )
        try:
            summary = self._call_llm(
                "Eres un asistente que resume conversaciones de forma concisa.",
                [{"role": "user", "content": prompt}])
            if summary:
                sess["summary"] = summary.strip()
                sess["history"] = recent
        except Exception:  # noqa: BLE001 — si falla el resumen, no recortamos
            pass

    # ----- LLM -----

    def _call_llm(self, system: str, history: list) -> str:
        if self.provider == "gemini":
            return _gemini_chat(self.model, system, history, self.temperature)
        if self.provider == "openai":
            return _openai_chat(self.model, system, history, self.temperature)
        return _ollama_chat(self.model, system, history, self.temperature)

    def _build_system(self, sess: dict) -> str:
        """System prompt + memoria a largo plazo (summary) + contexto en vivo."""
        parts = [SYSTEM_PROMPT]
        if sess.get("summary"):
            parts.append("=== RESUMEN DE LO HABLADO ANTES ===\n" + sess["summary"])
        context = ""
        if self._context_builder:
            try:
                context = self._context_builder() or ""
            except Exception as e:  # noqa: BLE001 — nunca tumbar el chat por esto
                context = f"(No se pudo leer el estado en vivo: {e})"
        if context:
            parts.append("=== ESTADO ACTUAL (datos en vivo del bot) ===\n" + context)
        return "\n\n".join(parts)

    def chat(self, session_id: str, message: str) -> str:
        """Procesa un mensaje del usuario y devuelve la respuesta del asistente.
        Guarda ambos en la memoria de la sesión."""
        message = (message or "").strip()
        if not message:
            return "¿En qué puedo ayudarte? Pregúntame por el estado de la cuenta, las posiciones o la mesa."

        sess = self._session(session_id)
        self._maybe_summarize(sess)

        system = self._build_system(sess)
        convo = sess["history"] + [{"role": "user", "content": message}]
        try:
            reply = self._call_llm(system, convo)
        except Exception as e:  # noqa: BLE001 — fail-safe conversacional
            return (f"Ahora mismo no puedo responder: el modelo ({self.provider}/{self.model}) "
                    f"no está disponible ({e}). Revisa que la GEMINI_API_KEY esté configurada "
                    f"en Ajustes.")

        if not reply:
            reply = "No he podido elaborar una respuesta. ¿Puedes reformular la pregunta?"

        sess["history"].append({"role": "user", "content": message})
        sess["history"].append({"role": "model", "content": reply})
        # Acota el historial literal (la memoria a largo plazo vive en summary).
        if len(sess["history"]) > self.MAX_TURNS:
            sess["history"] = sess["history"][-self.MAX_TURNS:]
        sess["updated"] = time.time()
        return reply


def build_live_context(state: dict, coordinator_overview: dict,
                       agents_overview: dict) -> str:
    """Construye el bloque de contexto en vivo (texto compacto) para el asistente.

    Función pura: recibe los datos ya recolectados (bot_state + overviews) y los
    resume. Tolerante a None / campos ausentes."""
    state = state or {}
    coordinator_overview = coordinator_overview or {}
    agents_overview = agents_overview or {}
    lines: list = []

    def money(v):
        try:
            return f"${float(v):,.2f}"
        except (TypeError, ValueError):
            return "—"

    def pct(v, dp=1):
        try:
            return f"{float(v) * 100:.{dp}f}%"
        except (TypeError, ValueError):
            return "n/a"

    # --- Cuenta ---
    acc = state.get("account_info") or {}
    lines.append("CUENTA:")
    lines.append(f"  Estado del bot: {'EN MARCHA' if state.get('bot_running') else 'PAUSADO'}"
                 f" · conexión: {'OK' if state.get('connected') else 'sin conexión'}")
    if acc:
        lines.append(f"  Equity {money(acc.get('equity'))} · Balance {money(acc.get('balance'))}"
                     f" · Margen libre {money(acc.get('free_margin'))}"
                     f" · Apalancamiento 1:{acc.get('leverage', '?')}")
    else:
        lines.append("  (sin datos de cuenta todavía)")

    # --- Mesa de dirección ---
    snap = (coordinator_overview.get("last_coordination") or {}).get("snapshot") or {}
    lines.append("")
    lines.append("MESA DE DIRECCIÓN:")
    lines.append(f"  Director LLM: {str(coordinator_overview.get('provider', '')).upper()}/"
                 f"{coordinator_overview.get('model', '?')}")
    if snap:
        lines.append(f"  Exposición total {pct(snap.get('total_exposure_pct'))}"
                     f" / tope {pct(snap.get('max_total_exposure_pct'), 0)}"
                     f" · P/L del día {pct(snap.get('daily_pnl_pct'), 2)}"
                     + ("  ⚠ COOLDOWN ACTIVO" if snap.get('in_cooldown') else ""))
        lines.append(f"  Cobertura (hedge): {'disponible' if snap.get('hedging') else 'no disponible'}"
                     f" · cierre automático: {'sí' if snap.get('can_close') else 'no'}")
    lines.append(f"  Última coordinación: {coordinator_overview.get('last_coordination_at') or 'pendiente'}"
                 f" · última junta: {coordinator_overview.get('last_junta_at') or 'pendiente'}")
    rationale = (coordinator_overview.get("last_coordination") or {}).get("rationale")
    if rationale:
        lines.append(f"  Razón de la mesa: {rationale}")
    decisions = (coordinator_overview.get("last_coordination") or {}).get("decisions") or []
    if decisions:
        lines.append("  Decisiones recientes:")
        for d in decisions:
            tag = "APROBADA" if d.get("approve") else "vetada"
            md = f"→{d['manage_direction']}" if d.get("manage_direction") else ""
            clamp = f" | {d['clamp']}" if d.get("clamp") else ""
            lines.append(f"    {d.get('symbol')}: {tag} · pos {d.get('position_action', 'hold')}{md}"
                         f" · asignación {pct(d.get('allocation_pct'), 0)}{clamp}")

    # --- Posiciones por símbolo (sesgo neto) ---
    symbols = snap.get("symbols") or {}
    if symbols:
        lines.append("")
        lines.append("POSICIONES POR SÍMBOLO:")
        for sym, s in symbols.items():
            nd = s.get("net_direction", "FLAT")
            lines.append(f"  {sym}: {s.get('long_positions', 0)}L/{s.get('short_positions', 0)}S"
                         f" · neto {nd} ({pct(s.get('net_exposure_pct'))})"
                         f" · P/L flotante {money(s.get('floating_pnl'))}"
                         f" · exposición {pct(s.get('exposure_pct'))}")

    # --- Posiciones abiertas (detalle) ---
    positions = state.get("positions") or {}
    if positions:
        total = sum((p.get("profit") or 0) for p in positions.values())
        lines.append("")
        lines.append(f"OPERACIONES ABIERTAS ({len(positions)}, P/L flotante total {money(total)}):")
        for p in list(positions.values())[:20]:
            lines.append(f"  {p.get('symbol')} {p.get('direction')} {p.get('volume')} lotes"
                         f" · {p.get('open_price')}→{p.get('current_price')}"
                         f" · P/L {money(p.get('profit'))}")

    # --- Señales actuales de los especialistas ---
    signals = state.get("signals") or {}
    if signals:
        lines.append("")
        lines.append("ÚLTIMAS SEÑALES DE LOS ESPECIALISTAS:")
        for sym, sig in signals.items():
            lines.append(f"  {sym}: {sig.get('action')} (conf {pct(sig.get('confidence'), 0)})"
                         f" · tendencia {sig.get('trend')} · riesgo {sig.get('risk_level')}")

    # --- Rendimiento de los agentes ---
    agents = agents_overview.get("agents") or []
    if agents:
        lines.append("")
        lines.append("AGENTES (rendimiento):")
        for a in agents:
            perf = a.get("performance") or {}
            stats = a.get("stats") or {}
            lines.append(f"  {a.get('name')} [{a.get('symbol')}] {str(a.get('provider', '')).upper()}/"
                         f"{a.get('model', '')}"
                         f" · señales {stats.get('signals', 0)}/trades {stats.get('trades', 0)}"
                         f" · win {pct(perf.get('win_rate'), 0)} ({perf.get('samples', 0)} muestras)")

    # --- Cierres recientes ---
    closed = state.get("closed_trades") or []
    if closed:
        total_pnl = sum((t.get("pnl") or 0) for t in closed)
        lines.append("")
        lines.append(f"CIERRES DE LA SESIÓN: {len(closed)} · P/L acumulado {money(total_pnl)}")
        for t in closed[-5:]:
            lines.append(f"  {t.get('symbol')} {t.get('action')} · P/L {money(t.get('pnl'))}")

    return "\n".join(lines)
