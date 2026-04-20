#Tests de integración

import pytest
import asyncio
import json
from unittest.mock import AsyncMock, patch, MagicMock
from pathlib import Path

from core.orchestrator import ArcadiumAutomation
from chains.arcadium_chains import ArcadiumChainBuilder
from utils.n8n_client import N8nClient, WorkflowExecutor
from core.state import MemoryStorage, StateManager
from validators.schemas import WebhookPayload


@pytest.mark.asyncio
async def test_full_integration_success():
    """Test integración completa exitosa"""
    # Mock de n8n client
    mock_n8n = MagicMock()
    mock_n8n.execute_webhook = AsyncMock(return_value={
        "status": "success",
        "data": {"response": "OK"}
    })

    # Setup storage
    storage = MemoryStorage()
    state_manager = StateManager(storage)

    # Mock del WorkflowExecutor
    mock_executor = MagicMock()
    mock_executor.execute_unified_arcadium = AsyncMock(return_value={
        "status": "success",
        "data": {"processed": True}
    })
    mock_executor.workflow_json_path = None

    # Build chain
    builder = ArcadiumChainBuilder(mock_executor, state_manager)
    chain = builder.build_unified_chain()

    # Payload de prueba
    payload = {
        "telefono": "+34612345678",
        "conversation": "Hola, test",
        "account_id": 1,
        "conversation_id": 100,
        "user_name": "Test User"
    }

    result = await chain.execute(payload)

    assert result['status'] == 'success'
    assert result['total_links'] > 0
    assert result['final_data'] is not None


@pytest.mark.asyncio
async def test_webhook_pipeline():
    """Test pipeline de webhook completo"""
    orchestrator = ArcadiumAutomation()

    # Mock internal components
    with patch.object(ArcadiumAutomation, 'initialize'):
        await orchestrator.initialize()

        # Mock n8n execute
        orchestrator.workflow_executor.execute_unified_arcadium = AsyncMock(
            return_value={"status": "success"}
        )

        payload = {
            "telefono": "+34612345678",
            "conversation": "Test message",
            "account_id": 1,
            "conversation_id": 123
        }

        result = await orchestrator.process_webhook(payload)

        assert 'status' in result
        assert 'chain_name' in result

        await orchestrator.shutdown()


def test_valid_webhook_payload_parsing():
    """Test parsing de payload webhook"""
    # Payload Chatwoot
    payload = {
        "body": {
            "conversation": {
                "messages": [
                    {
                        "sender": {
                            "phone_number": "+34612345678",
                            "name": "Usuario Test"
                        },
                        "content": "Mensaje de prueba"
                    }
                ]
            }
        },
        "account_id": 1
    }

    webhook = WebhookPayload(
        body=payload["body"],
        account_id=payload["account_id"]
    )

    conversation = webhook.extract_conversation()

    assert conversation.phone == "+34612345678"
    assert conversation.user_name == "Usuario Test"
    assert conversation.account_id == 1
    assert conversation.messages[0].content == "Mensaje de prueba"


def test_audio_payload_parsing():
    """Test parsing de payload con audio"""
    payload = {
        "body": {
            "conversation": {
                "messages": [
                    {
                        "attachments": [
                            {
                                "file_type": "audio",
                                "url": "https://example.com/audio.ogg"
                            }
                        ]
                    }
                ]
            }
        },
        "account_id": 1
    }

    webhook = WebhookPayload(
        body=payload["body"],
        account_id=payload["account_id"]
    )

    conversation = webhook.extract_conversation()
    message = conversation.messages[0]

    assert message.message_type == "audio"
    assert len(message.attachments) > 0
    assert message.attachments[0]['file_type'] == "audio"


@pytest.mark.asyncio
async def test_chain_metrics_tracking():
    """Test tracking de métricas en cadena"""
    storage = MemoryStorage()
    state_manager = StateManager(storage)

    builder = ArcadiumChainBuilder(None, state_manager)
    chain = builder.build_unified_chain()

    # Ejecutar dos veces
    await chain.execute({"test": "data1"})
    await chain.execute({"test": "data2"})

    metrics = chain.get_metrics()

    assert metrics['total_executions'] == 2
    assert metrics['successful_executions'] >= 1
    assert len(metrics['link_metrics']) > 0


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
