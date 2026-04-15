# Tests del sistema Landchain

import pytest
import asyncio
import time
from core.landchain import LandChain, ChainLink, ChainResult, ChainStatus, retryable


@pytest.mark.asyncio
async def test_chain_success():
    """Test cadena exitosa simple"""
    chain = LandChain(name="test_success", max_retries=1)

    async def success_func(data, context):
        return {"processed": True, "data": data}

    chain.add_link("success_link", success_func)
    result = await chain.execute({"initial": "data"})

    assert result['status'] == 'success'
    assert result['successful_links'] == 1
    assert result['final_data']['processed'] is True


@pytest.mark.asyncio
async def test_chain_with_validation():
    """Test cadena con validador"""
    chain = LandChain(name="test_validation", strict_mode=True)

    def validate_positive(value):
        if value <= 0:
            raise ValueError("Value must be positive")

    def multiply_by_two(data, context):
        return data * 2

    chain.add_link(
        "validate_and_multiply",
        multiply_by_two,
        validator=validate_positive
    )

    # Valor válido
    result = await chain.execute(5)
    assert result['status'] == 'success'
    assert result['final_data'] == 10

    # Valor inválido
    result = await chain.execute(-1)
    assert result['status'] == 'failed'
    assert len(result['results']) == 1
    assert result['results'][0]['status'] == 'failed'


@pytest.mark.asyncio
async def test_chain_with_retries():
    """Test reintentos automáticos"""
    chain = LandChain(name="test_retries", max_retries=2)

    attempt_count = 0

    async def flaky_func(data, context):
        nonlocal attempt_count
        attempt_count += 1
        if attempt_count < 3:
            raise ValueError("Temporary error")
        return {"attempts": attempt_count}

    chain.add_link("flaky_link", flaky_func, max_retries=2)
    result = await chain.execute({})

    assert result['status'] == 'success'
    assert result['final_data']['attempts'] == 3
    assert result['results'][0]['retry_count'] == 2


@pytest.mark.asyncio
async def test_chain_timeout():
    """Test timeout en cadena"""
    chain = LandChain(name="test_timeout", timeout=0.5)

    async def slow_func(data, context):
        await asyncio.sleep(2)
        return {"done": True}

    chain.add_link("slow_link", slow_func, timeout=1.0)
    result = await chain.execute({})

    assert result['status'] == 'failed'
    assert any("timeout" in r['error'].lower() for r in result['results'])


@pytest.mark.asyncio
async def test_chain_continue_on_failure():
    """Test continuación tras fallo"""
    chain = LandChain(name="test_continue", strict_mode=False)

    async def fail_func(data, context):
        raise ValueError("Intentional failure")

    async def second_func(data, context):
        return {"second": True, "input": data}

    chain.add_link("fail_link", fail_func, continue_on_failure=True)
    chain.add_link("second_link", second_func)

    result = await chain.execute({})

    assert result['status'] == 'success'  # Continúa a pesar del fallo
    assert result['successful_links'] == 1  # Solo segundo link exitoso
    assert result['failed_links'] == 1  # Primer link falló


@pytest.mark.asyncio
async def test_chain_rollback():
    """Test rollback en fallo"""
    chain = LandChain(name="test_rollback")

    actions = []

    async def main_func(data, context):
        actions.append("main")
        return {"processed": True}

    async def rollback_func(data, context):
        actions.append("rollback")

    chain.add_link(
        "with_rollback",
        main_func,
        rollback_on_failure=True,
        rollback_func=rollback_func
    )

    # Test exitoso
    actions.clear()
    result = await chain.execute({})
    assert result['status'] == 'success'
    assert "main" in actions
    assert "rollback" not in actions

    # Test con fallo
    actions.clear()

    async def failing_func(data, context):
        actions.append("fail_main")
        raise ValueError("Fail")

    chain.links[0] = ChainLink(
        name="failing",
        func=failing_func,
        rollback_on_failure=True,
        rollback_func=rollback_func
    )

    result = await chain.execute({})
    assert result['status'] == 'failed'
    assert "fail_main" in actions
    assert "rollback" in actions


@pytest.mark.asyncio
async def test_chain_metrics():
    """Test recolección de métricas"""
    chain = LandChain(name="test_metrics")

    async def quick_func(data, context):
        await asyncio.sleep(0.01)
        return {"done": True}

    chain.add_link("quick", quick_func)

    # Ejecutar múltiples veces
    for _ in range(3):
        await chain.execute({})

    metrics = chain.get_metrics()

    assert metrics['total_executions'] == 3
    assert metrics['successful_executions'] == 3
    assert metrics['link_metrics']['quick']['executions'] == 3
    assert metrics['link_metrics']['quick']['successes'] == 3


def test_retryable_decorator():
    """Test decorador retryable"""
    attempt_count = 0

    @retryable(max_retries=2, delay=0.1)
    async def flaky_function():
        nonlocal attempt_count
        attempt_count += 1
        if attempt_count < 3:
            raise ValueError("Retryable error")
        return "success"

    result = asyncio.run(flaky_function())
    assert result == "success"
    assert attempt_count == 3


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
