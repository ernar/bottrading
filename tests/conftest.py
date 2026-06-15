"""Fixtures de tests: una base de datos SQLite limpia por test.

Cada test recibe un archivo SQLite temporal recién inicializado, de modo que la
persistencia (señales, memoria, first_seen del RiskBook) queda aislada entre
tests sin tocar la DB real del proyecto.
"""
import pytest

from core import db


@pytest.fixture(autouse=True)
def fresh_db(tmp_path):
    url = f"sqlite:///{(tmp_path / 'test.db').as_posix()}"
    db.init_db(url)
    yield
