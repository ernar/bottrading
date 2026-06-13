"""Sistema agéntico: un agente especializado por símbolo.

Cada agente encapsula su símbolo, su modelo LLM, sus umbrales de riesgo,
su persona (contexto de especialización inyectado en el prompt) y su propia
memoria de señales. El orquestador (orchestrator.py) coordina varios agentes
y, más adelante, ajustará sus parámetros para optimizar resultados.
"""
from agents.base_agent import AgentParams, SymbolAgent
from agents.registry import build_agent, list_agents, AGENT_BLUEPRINTS
from agents.orchestrator import AgentOrchestrator

__all__ = [
    "AgentParams",
    "SymbolAgent",
    "build_agent",
    "list_agents",
    "AGENT_BLUEPRINTS",
    "AgentOrchestrator",
]
