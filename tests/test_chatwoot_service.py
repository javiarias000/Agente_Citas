#!/usr/bin/env python3
"""
Tests para ChatwootService
"""

import pytest
import json
from unittest.mock import AsyncMock, patch, MagicMock
from services.chatwoot_service import (
    ChatwootService,
    ChatwootMessage,
    ChatwootError,
    parse_webhook_payload,
    normalize_contact,
    verify_webhook_signature
)


class TestChatwootMessage:
    """Tests para ChatwootMessage"""

    def test_init(self):
        msg = ChatwootMessage(
            conversation_id="123",
            content="Hola",
            message_type="text",
            private=False
        )
        assert msg.conversation_id == "123"
        assert msg.content == "Hola"
        assert msg.message_type == "text"
        assert msg.private is False

    def test_to_payload_text(self):
        msg = ChatwootMessage(
            conversation_id="123",
            content="Test message",
            message_type="text"
        )
        payload = msg.to_payload()
        assert payload["content"] == "Test message"
        assert payload["message_type"] == "text"
        assert payload["private"] is False

    def test_to_payload_private(self):
        msg = ChatwootMessage(
            conversation_id="123",
            content="Private note",
            private=True
        )
        payload = msg.to_payload()
        assert payload["private"] is True


class TestParseWebhookPayload:
    """Tests para parse_webhook_payload"""

    def test_parse_message_created(self):
        payload = {
            "event": "message_created",
            "payload": {
                "id": "msg_001",
                "content": "Hola, quiero una cita",
                "message_type": "text",
                "sender": {
                    "id": "contact_001",
                    "phone_number": "+549123456789",
                    "email": None,
                    "name": "Juan Perez"
                },
                "conversation": {
                    "id": "conv_001",
                    "inbox_id": 6,
                    "account_id": 1
                },
                "meta": {
                    "source": "whatsapp"
                }
            }
        }

        result = parse_webhook_payload(payload)

        assert result is not None
        assert result["event"] == "message_created"
        assert result["conversation_id"] == "conv_001"
        assert result["account_id"] == 1
        assert result["inbox_id"] == 6
        assert result["message_id"] == "msg_001"
        assert result["message_type"] == "text"
        assert result["content"] == "Hola, quiero una cita"
        assert result["sender_type"] == "contact"
        assert result["contact"]["phone_number"] == "+549123456789"
        assert "raw" in result

    def test_ignore_agent_message(self):
        payload = {
            "event": "message_created",
            "payload": {
                "id": "msg_002",
                "content": "This is from agent",
                "message_type": "text",
                "sender": {
                    "id": "agent_001",
                    "type": "agent",  # <-- agente
                    "name": "Agent"
                },
                "conversation": {
                    "id": "conv_002",
                    "account_id": 1
                }
            }
        }

        result = parse_webhook_payload(payload)
        assert result is None  # Debe ignorar mensajes del agente

    def test_invalid_payload_no_event(self):
        payload = {"payload": {"something": "else"}}
        result = parse_webhook_payload(payload)
        assert result is None

    def test_invalid_payload_no_conversation(self):
        payload = {
            "event": "message_created",
            "payload": {
                "content": "Test",
                "sender": {"type": "contact"}
            }
        }
        result = parse_webhook_payload(payload)
        assert result is None

    def test_empty_content(self):
        payload = {
            "event": "message_created",
            "payload": {
                "id": "msg_003",
                "content": "",
                "sender": {"type": "contact"},
                "conversation": {"id": "conv_003"}
            }
        }
        result = parse_webhook_payload(payload)
        assert result is None


class TestNormalizeContact:
    """Tests para normalize_contact"""

    def test_phone_number(self):
        contact = {"phone_number": "+549123456789"}
        normalized = normalize_contact(contact)
        # normalize_phone debería limpiar y dejar solo dígitos
        assert "549" in normalized or "123456789" in normalized

    def test_email_fallback(self):
        contact = {"email": "test@example.com"}
        normalized = normalize_contact(contact)
        assert normalized == "test@example.com"

    def test_identifier_fallback(self):
        contact = {"identifier": "user_12345"}
        normalized = normalize_contact(contact)
        assert normalized == "user_12345"

    def test_prefer_phone_over_email(self):
        contact = {
            "phone_number": "+549123456789",
            "email": "test@example.com"
        }
        normalized = normalize_contact(contact)
        # Debería preferir phone
        assert "+549" in normalized or "123456789" in normalized

    def test_no_contact_data_raises(self):
        contact = {}
        with pytest.raises(ValueError):
            normalize_contact(contact)


class TestVerifyWebhookSignature:
    """Tests para verify_webhook_signature"""

    def test_valid_signature(self):
        secret = "my_secret"
        payload = b'{"test": "data"}'
        signature = "sha256=" + hashlib.sha256(secret.encode() + payload).hexdigest()

        result = verify_webhook_signature(payload, signature, secret)
        assert result is True

    def test_invalid_signature(self):
        secret = "my_secret"
        payload = b'{"test": "data"}'
        signature = "sha256=wrong_hash"

        result = verify_webhook_signature(payload, signature, secret)
        assert result is False

    def test_missing_signature(self):
        payload = b'{"test": "data"}'
        result = verify_webhook_signature(payload, "", "secret")
        assert result is False

    def test_missing_secret(self):
        payload = b'{"test": "data"}'
        signature = "sha256=abc123"
        result = verify_webhook_signature(payload, signature, "")
        assert result is False

    def test_sha256_prefix_stripped(self):
        secret = "my_secret"
        payload = b'{"test": "data"}'
        # Sin prefijo sha256=
        correct_hash = hashlib.sha256(secret.encode() + payload).hexdigest()

        result = verify_webhook_signature(payload, correct_hash, secret)
        assert result is True


class TestChatwootServiceUnit:
    """Tests unitarios de ChatwootService (sin llamadas de red)"""

    @pytest.fixture
    def mock_settings(self):
        settings = MagicMock()
        settings.CHATWOOT_API_URL = "https://chatwoot.example.com"
        settings.CHATWOOT_API_TOKEN = "test_token"
        settings.CHATWOOT_ACCOUNT_ID = 1
        return settings

    def test_init(self, mock_settings):
        service = ChatwootService(mock_settings)
        assert service.base_url == "https://chatwoot.example.com"
        assert service.token == "test_token"
        assert service.account_id == 1

    def test_init_no_trailing_slash(self, mock_settings):
        mock_settings.CHATWOOT_API_URL = "https://chatwoot.example.com/"
        service = ChatwootService(mock_settings)
        assert service.base_url == "https://chatwoot.example.com"

    @pytest.mark.asyncio
    async def test_send_message_not_implemented(self, mock_settings):
        service = ChatwootService(mock_settings)
        # No conectamos, solo testeamos estructura
        msg = ChatwootMessage("123", "Hello")
        # Mock del client
        service.client = AsyncMock()
        service.client.post = AsyncMock(return_value=MagicMock(
            status_code=200,
            json=lambda: {"id": 456}
        ))

        result = await service.send_message(msg)

        assert result["success"] is True
        assert result["message_id"] == 456

    @pytest.mark.asyncio
    async def test_send_text(self, mock_settings):
        service = ChatwootService(mock_settings)
        service.client = AsyncMock()
        service.client.post = AsyncMock(return_value=MagicMock(
            status_code=200,
            json=lambda: {"id": 789}
        ))

        result = await service.send_text("conv_123", "Hello world")

        assert result["success"] is True
        # Verificar que se llamó al endpoint correcto
        call_args = service.client.post.call_args
        assert "/accounts/1/conversations/conv_123/messages" in str(call_args)
