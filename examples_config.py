#!/usr/bin/env python
"""Demostración: Configuración de límites de operaciones por símbolo/modelo.

Ejecutar con:
    python examples_config.py

Muestra cómo funciona la precedencia de configuración .env para los parámetros
del agente, especialmente MAX_OPEN_POSITIONS.
"""
import os
import sys
from pathlib import Path

# Asegurarse de que podemos importar desde el root
project_root = Path(__file__).parent
sys.path.insert(0, str(project_root))

from dotenv import load_dotenv
from core.config import get_agent_param_overrides, get_max_open_positions


def demo_precedence():
    """Demuestra el sistema de precedencia de configuración."""
    print("\n" + "=" * 70)
    print("DEMOSTRACIÓN: Configuración de Límites de Operaciones")
    print("=" * 70)
    
    load_dotenv()  # Cargar .env actual
    
    print("\n📋 CONFIGURACIÓN EN .env:")
    print("-" * 70)
    
    # Mostrar variables relevantes
    config_vars = [
        "MAX_OPEN_POSITIONS_BTCUSD",
        "MAX_OPEN_POSITIONS_EURUSD", 
        "MAX_OPEN_POSITIONS_QWEN3_8B",
        "MAX_OPEN_POSITIONS_GPT4",
        "MAX_OPEN_POSITIONS_DEFAULT",
        "MIN_CONFIDENCE_BTCUSD",
        "MIN_CONFIDENCE_DEFAULT",
        "MIN_RR_DEFAULT",
    ]
    
    for var in config_vars:
        val = os.getenv(var)
        if val:
            print(f"  {var:35s} = {val}")
    
    print("\n  (Si no ves configuración, edita .env y vuelve a ejecutar este script)")
    
    print("\n🔍 RESOLUCIÓN DE PRECEDENCIA:")
    print("-" * 70)
    
    # Casos de prueba: (símbolo, modelo, descripción)
    test_cases = [
        ("BTCUSD", "qwen3:8b", "BTC con modelo Qwen (ambos configurados)"),
        ("BTCUSD", "gpt-4", "BTC con GPT-4 (solo símbolo configurado)"),
        ("EURUSD", "qwen3:8b", "EUR con Qwen (solo modelo configurado)"),
        ("EURUSD", "gemini", "EUR con Gemini (sin configuración específica)"),
        ("GBPUSD", "llama2", "GBP con Llama2 (sin configuración específica)"),
    ]
    
    for symbol, model, description in test_cases:
        max_pos = get_max_open_positions(symbol, model)
        print(f"\n  {symbol:7} + {model:15s} = {max_pos} operaciones máx")
        print(f"    Descripción: {description}")

    print("\n📦 OVERRIDES MÚLTIPLES:")
    print("-" * 70)
    
    overrides_btc = get_agent_param_overrides("BTCUSD", "qwen3:8b")
    if overrides_btc:
        print(f"\n  Parámetros configurados para BTCUSD + qwen3:8b:")
        for key, val in sorted(overrides_btc.items()):
            print(f"    • {key:30s} = {val}")
    else:
        print(f"\n  ⓘ No hay overrides configurados en .env para BTCUSD")
    
    print("\n💡 EJEMPLO DE CONFIGURACIÓN RECOMENDADA EN .env:")
    print("-" * 70)
    
    example_config = """
# Limitar operaciones máximas por símbolo
MAX_OPEN_POSITIONS_BTCUSD=2        # Bitcoin: más agresivo (2 máx)
MAX_OPEN_POSITIONS_EURUSD=3        # Euro: conservador (3 máx)

# Limitar por modelo si tienes múltiples
MAX_OPEN_POSITIONS_GPT4=4          # GPT-4: puede abrir más posiciones
MAX_OPEN_POSITIONS_QWEN3_8B=3      # Qwen: más conservador

# Fallback global (si no hay config específica)
MAX_OPEN_POSITIONS_DEFAULT=5

# Otros parámetros por símbolo
MIN_CONFIDENCE_BTCUSD=0.7
MIN_RR_BTCUSD=2.0
ATR_SL_MULT_BTCUSD=1.8

# O defaults globales
MIN_CONFIDENCE_DEFAULT=0.6
MIN_RR_DEFAULT=1.5
    """
    
    print(example_config)
    
    print("\n✅ CÓMO USAR:")
    print("-" * 70)
    print("""
  1. Edita tu archivo .env con la configuración deseada
  2. Los agentes cargarán automáticamente los valores al arrancar
  3. La precedencia es:  símbolo > modelo > default > hardcoded
  
  Vuelve a ejecutar este script para ver la nueva configuración en efecto.
    """)
    
    print("=" * 70 + "\n")


if __name__ == "__main__":
    demo_precedence()
