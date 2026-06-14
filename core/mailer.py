"""Envío del reporte por correo (SMTP, stdlib).

El envío está **apagado por defecto** (`SMTP_ENABLED=false`): mientras no haya
credenciales, `send_report` genera el log y devuelve False sin tocar la red. Al
activarlo, usa smtplib con STARTTLS. Es fail-safe: cualquier error de red se
registra y devuelve False, nunca tumba el bot.
"""
import smtplib
from email.message import EmailMessage
from typing import Optional

from core.config import get_schedule_config


def send_report(subject: str, text: str, html: Optional[str] = None,
                cfg: Optional[dict] = None) -> bool:
    """Envía el reporte por SMTP si está habilitado. Devuelve True si se envió.

    Si `SMTP_ENABLED=false` (default), imprime un aviso y devuelve False sin
    intentar conectar. `cfg` permite inyectar la configuración en tests."""
    cfg = cfg or get_schedule_config()

    if not cfg.get("smtp_enabled"):
        print("  [REPORTE] Email desactivado (SMTP_ENABLED=false): se generó el "
              "reporte pero no se envió.")
        return False

    to_addr = cfg.get("report_email_to", "").strip()
    host = cfg.get("smtp_host", "").strip()
    if not host or not to_addr:
        print("  [REPORTE] SMTP habilitado pero falta SMTP_HOST o REPORT_EMAIL_TO; "
              "no se envía.")
        return False

    from_addr = cfg.get("smtp_from") or cfg.get("smtp_user") or to_addr
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = from_addr
    msg["To"] = to_addr
    msg.set_content(text)
    if html:
        msg.add_alternative(html, subtype="html")

    try:
        with smtplib.SMTP(host, cfg.get("smtp_port", 587), timeout=20) as server:
            if cfg.get("smtp_use_tls", True):
                server.starttls()
            user = cfg.get("smtp_user", "").strip()
            if user:
                server.login(user, cfg.get("smtp_password", ""))
            server.send_message(msg)
        print(f"  [REPORTE] Enviado a {to_addr}.")
        return True
    except Exception as exc:  # noqa: BLE001 — fail-safe: nunca tumbar el bot
        print(f"  [REPORTE] Error al enviar el correo: {exc}")
        return False
