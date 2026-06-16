"""Tests del asistente (responsable de la organización): extracción de la línea
con marcador `NOTA_MESA:` (nota de dirección para la mesa) del cuerpo del texto,
sin interferir con la línea de `SUGERENCIAS:`."""
from core.assistant import _split_note, _split_suggestions


def test_sin_nota_devuelve_none():
    body, note = _split_note("Vamos bien, equity estable.")
    assert note is None
    assert body == "Vamos bien, equity estable."


def test_extrae_nota_y_la_quita_del_cuerpo():
    raw = "He tomado nota.\nNOTA_MESA: sé cauto con BTC esta semana\nresto"
    body, note = _split_note(raw)
    assert note == "sé cauto con BTC esta semana"
    assert "NOTA_MESA" not in body
    assert "He tomado nota." in body and "resto" in body


def test_nota_de_retirada():
    body, note = _split_note("Vale.\nNOTA_MESA: borrar")
    assert note == "borrar"
    assert body == "Vale."


def test_nota_y_sugerencias_conviven():
    # La nota se extrae primero; las sugerencias después, del cuerpo restante.
    raw = ("Cuenta sana.\n"
           "NOTA_MESA: prioriza cerrar cortos\n"
           "SUGERENCIAS: ¿Cómo vamos? | ¿Riesgo actual?")
    body, note = _split_note(raw)
    assert note == "prioriza cerrar cortos"
    reply, suggestions = _split_suggestions(body)
    assert reply == "Cuenta sana."
    assert suggestions == ["¿Cómo vamos?", "¿Riesgo actual?"]


def test_solo_primera_nota_se_toma():
    raw = "NOTA_MESA: primera\nNOTA_MESA: segunda"
    body, note = _split_note(raw)
    assert note == "primera"
    # La segunda línea queda en el cuerpo (no se procesa como nota).
    assert "NOTA_MESA: segunda" in body
