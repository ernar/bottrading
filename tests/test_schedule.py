"""Tests del helper de cadencias del orquestador (`_due`)."""
from agents.orchestrator import AgentOrchestrator


def test_due_not_elapsed():
    # Sólo han pasado 10s de un intervalo de 60 -> aún no vence.
    assert AgentOrchestrator._due(last=100.0, interval=60, now=110.0) is False


def test_due_elapsed():
    assert AgentOrchestrator._due(last=100.0, interval=60, now=161.0) is True


def test_due_exactly_at_interval():
    # Igualdad cuenta como vencido (>=).
    assert AgentOrchestrator._due(last=100.0, interval=60, now=160.0) is True


def test_due_zero_interval_disabled():
    # interval 0 = tarea desactivada, nunca vence.
    assert AgentOrchestrator._due(last=0.0, interval=0, now=10_000.0) is False
