import pytest
import asyncio
import sys
from pathlib import Path

# Añadir directorio raíz al path
sys.path.insert(0, str(Path(__file__).parents[1]))

from core.config import Settings
from core.state import MemoryStorage, StateManager
from core.landchain import LandChain, ChainLink, ChainResult, ChainStatus


@pytest.fixture
def memory_storage():
    """Storage en memoria para tests"""
    return MemoryStorage()


@pytest.fixture
def state_manager(memory_storage):
    """State manager para tests"""
    return StateManager(memory_storage)


@pytest.fixture
def test_settings():
    """Configuración de test"""
    return Settings(
        CHAIN_MAX_RETRIES=1,
        CHAIN_TIMEOUT=5.0,
        STRICT_VALIDATION=True,
        LOG_LEVEL="DEBUG"
    )


@pytest.fixture
def event_loop():
    """Event loop para tests asíncronos"""
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()
