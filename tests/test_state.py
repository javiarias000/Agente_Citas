# Tests de gestión de estado


import pytest
import asyncio
from datetime import datetime, timedelta
from core.state import (
    StateStorage, MemoryStorage, StateManager,
    StateKeys, StateError
)


@pytest.mark.asyncio
async def test_memory_storage_basic():
    """Test storage memoria básico"""
    storage = MemoryStorage()

    # Guardar
    await storage.save("key1", "value1")
    assert await storage.exists("key1")

    # Cargar
    value = await storage.load("key1")
    assert value == "value1"

    # Eliminar
    await storage.delete("key1")
    assert not await storage.exists("key1")

    # Clave inexistente
    assert await storage.load("nonexistent") is None


@pytest.mark.asyncio
async def test_memory_storage_ttl():
    """Test storage memoria con TTL"""
    storage = MemoryStorage(ttl_seconds=1)

    await storage.save("key_ttl", "value", ttl=1)
    assert await storage.load("key_ttl") == "value"

    # Esperarexpiración
    await asyncio.sleep(1.1)

    assert await storage.load("key_ttl") is None


@pytest.mark.asyncio
async def test_state_manager_cache():
    """Test state manager con caché"""
    storage = MemoryStorage()
    manager = StateManager(storage)

    # Guardar
    await manager.set("test", {"data": "value"}, cache=True)

    # Get desde caché
    value = await manager.get("test")
    assert value == {"data": "value"}

    # Invalitar caché
    await manager.delete("test")
    assert await manager.get("test") is None

    # Limpiar caché
    await manager.set("test1", "v1", cache=True)
    await manager.set("test2", "v2", cache=True)
    await manager.clear_cache()
    assert await manager.get("test1") == "v1"  # Carga desde storage


@pytest.mark.asyncio
async def test_state_manager_get_or_create():
    """Test get or create"""
    storage = MemoryStorage()
    manager = StateManager(storage)

    call_count = 0

    async def factory():
        nonlocal call_count
        call_count += 1
        return {"created": True, "call": call_count}

    # Primera vez: crea
    value1 = await manager.get_or_create("factory_key", factory)
    assert value1['created'] is True
    assert call_count == 1

    # Segunda vez: usa cache
    value2 = await manager.get_or_create("factory_key", factory)
    assert value2['created'] is True
    assert call_count == 1  # No se llama de nuevo


@pytest.mark.asyncio
async def test_state_keys_generation():
    """Test generación de claves"""
    phone_key = StateKeys.conversation("+34612345678")
    assert phone_key == "conversation:+34612345678"

    processing_key = StateKeys.processing("conv_123")
    assert processing_key == "processing:conv_123"

    transcription_key = StateKeys.transcription("+34612345678")
    assert transcription_key == "transcription:+34612345678"


@pytest.mark.asyncio
async def test_state_manager_ttl():
    """Test TTL en state manager"""
    storage = MemoryStorage()
    manager = StateManager(storage)

    await manager.set("short_lived", "value", ttl=1)
    assert await manager.get("short_lived") == "value"

    await asyncio.sleep(1.1)
    assert await manager.get("short_lived") is None


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
