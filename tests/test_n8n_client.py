# Tests del cliente n8n


import pytest
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch
import aiohttp
from aiohttp import web

from utils.n8n_client import N8nClient, WorkflowExecutor


@pytest.fixture
def mock_session():
    """Mock de sesión HTTP"""
    session = MagicMock()
    session.request = MagicMock()  # request es síncrono, devuelve contexto asincrónico
    session.close = AsyncMock()
    session.closed = False
    return session


@pytest.mark.asyncio
async def test_n8n_client_execute_webhook_success(mock_session):
    """Test ejecución exitosa de webhook"""
    mock_response = MagicMock()
    mock_response.status = 200
    mock_response.json = AsyncMock(return_value={"status": "success"})

    mock_session.request.return_value.__aenter__ = AsyncMock(return_value=mock_response)
    mock_session.request.return_value.__aexit__ = AsyncMock(return_value=None)

    client = N8nClient(base_url="http://test:5678")
    client._session = mock_session

    result = await client.execute_webhook("test_webhook", {"key": "value"})

    assert result == {"status": "success"}
    mock_session.request.assert_called_once()


@pytest.mark.asyncio
async def test_n8n_client_execute_webhook_error(mock_session):
    """Test error en webhook"""
    mock_response = MagicMock()
    mock_response.status = 500
    mock_response.json = AsyncMock(return_value={"error": "Internal Server Error"})

    mock_session.request.return_value.__aenter__ = AsyncMock(return_value=mock_response)
    mock_session.request.return_value.__aexit__ = AsyncMock(return_value=None)

    client = N8nClient(base_url="http://test:5678")
    client._session = mock_session

    from utils.n8n_client import APIError
    with pytest.raises(APIError) as exc:
        await client.execute_webhook("test_webhook", {"key": "value"})

    assert exc.value.details.get("status_code") == 500


@pytest.mark.asyncio
async def test_n8n_client_retry_logic(mock_session):
    """Test lógica de reintentos"""
    # Fallar 2 veces, luego éxito
    mock_response_fail = MagicMock()
    mock_response_fail.status = 500
    mock_response_fail.json = AsyncMock(return_value={"error": "Server error"})

    mock_response_success = MagicMock()
    mock_response_success.status = 200
    mock_response_success.json = AsyncMock(return_value={"status": "success"})

    # Configurar side effect: fallar con ClientError las dos primeras veces
    call_count = 0
    def mock_request(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count <= 2:
            raise aiohttp.ClientError("Server error")
        else:
            cm = MagicMock()
            cm.__aenter__ = AsyncMock(return_value=mock_response_success)
            cm.__aexit__ = AsyncMock(return_value=None)
            return cm

    mock_session.request.side_effect = mock_request

    client = N8nClient(base_url="http://test:5678", max_retries=3)
    client._session = mock_session

    result = await client.execute_webhook("test_webhook", {"key": "value"})

    assert result == {"status": "success"}
    assert call_count == 3


def test_workflow_executor_get_webhook_mapping():
    """Test extracción de mapeo de webhooks"""
    workflow_config = {
        "name": "Test Workflow",
        "nodes": [
            {
                "type": "n8n-nodes-base.webhook",
                "name": "Webhook Chatwoot",
                "webhookId": "abc123"
            },
            {
                "type": "n8n-nodes-base.set",
                "name": "Set Variables"
            }
        ]
    }

    with patch.object(WorkflowExecutor, '__init__', return_value=None):
        executor = WorkflowExecutor(None, None)
        executor._workflow_config = workflow_config

        mapping = executor.get_webhook_mapping()

        assert "Webhook Chatwoot" in mapping
        assert mapping["Webhook Chatwoot"] == "abc123"
        assert "Set Variables" not in mapping


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
