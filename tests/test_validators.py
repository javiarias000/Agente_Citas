# Tests de validadores y schemas


import asyncio
import pytest
from validators.schemas import (
    PhoneNumber, Message, Conversation, WebhookPayload,
    validate_required_fields, validate_phone_number,
    sanitize_text, ValidatorChain, ValidationError
)


def test_phone_number_valid():
    """Test número teléfono válido"""
    phone = PhoneNumber(number="1234567890", country_code="+34")
    assert phone.full_number() == "+341234567890"
    assert len(phone.number) == 10


def test_phone_number_invalid():
    """Test número teléfono inválido"""
    with pytest.raises(ValueError):
        PhoneNumber(number="123", country_code="+34")  # Muy corto

    with pytest.raises(ValueError):
        PhoneNumber(number="abc1234567", country_code="+34")  # No solo dígitos


def test_message_valid():
    """Test mensaje válido"""
    msg = Message(
        content="Hola mundo",
        message_type="text"
    )
    assert msg.content == "Hola mundo"
    assert msg.message_type == "text"


def test_message_invalid():
    """Test mensaje inválido"""
    with pytest.raises(ValueError):
        Message(content="", message_type="text")  # Vacío

    with pytest.raises(ValueError):
        Message(content="   ", message_type="text")  # Solo espacios

    with pytest.raises(ValueError):
        Message(content="x" * 5000, message_type="text")  # Muy largo


def test_conversation_valid():
    """Test conversación válida"""
    conv = Conversation(
        conversation_id=123,
        account_id=456,
        phone="+34612345678",
        user_name="Test User",
        messages=[
            Message(content="Hola", message_type="text")
        ]
    )
    assert conv.conversation_id == 123
    assert len(conv.messages) == 1


def test_webhook_payload_extraction():
    """Test extracción de WebhookPayload"""
    payload = WebhookPayload(
        telefono="+34612345678",
        chat="Hola, necesito ayuda",
        account_id=1,
        conversation_id=100,
        user_name="Usuario"
    )

    conversation = payload.extract_conversation()

    assert conversation.phone == "+34612345678"
    assert conversation.conversation_id == 100
    assert conversation.account_id == 1
    assert conversation.user_name == "Usuario"
    assert len(conversation.messages) == 1
    assert conversation.messages[0].content == "Hola, necesito ayuda"


def test_webhook_payload_from_body():
    """Test extracción desde body anidado"""
    payload = WebhookPayload(
        body={
            "conversation": {
                "conversation_id": 123,
                "messages": [
                    {
                        "sender": {
                            "phone_number": "+34612345678",
                            "name": "Test"
                        },
                        "content": "Test message"
                    }
                ]
            }
        },
        account_id=1
    )

    conversation = payload.extract_conversation()

    assert conversation.phone == "+34612345678"
    assert conversation.user_name == "Test"
    assert conversation.messages[0].content == "Test message"
    assert conversation.conversation_id == 123


def test_validate_required_fields():
    """Test validación de campos requeridos"""
    data = {"name": "test", "age": 25}

    # Válido
    validate_required_fields(data, ["name", "age"])

    # Inválido
    with pytest.raises(ValidationError) as exc:
        validate_required_fields(data, ["name", "email"])
    assert "email" in str(exc.value)


def test_validate_phone_number():
    """Test validación telefónica"""
    # Válidos
    assert validate_phone_number("+34612345678")
    assert validate_phone_number("612345678")
    assert validate_phone_number("+12125551234")

    # Inválidos
    assert not validate_phone_number("123")
    assert not validate_phone_number("abc123")
    assert not validate_phone_number("")


def test_sanitize_text():
    """Test sanitización de texto"""
    # Espacios múltiples
    assert sanitize_text("Hello    world") == "Hello world"

    # Trim
    assert sanitize_text("  test  ") == "test"

    # Límite de longitud
    long_text = "x" * 5000
    result = sanitize_text(long_text, max_length=100)
    assert len(result) <= 100
    assert result.endswith("...")

    # Vacío
    assert sanitize_text("") == ""
    assert sanitize_text(None) == ""


def test_validator_chain():
    """Test cadena de validadores"""
    chain = ValidatorChain(strict=True)

    def validate_even(value):
        if value % 2 != 0:
            raise ValueError("Must be even")

    def validate_positive(value):
        if value < 0:
            raise ValueError("Must be positive")

    chain.add_validator(validate_positive, "positive")
    chain.add_validator(validate_even, "even")

    # Valor válido
    result = asyncio.run(chain.validate(4))
    assert result['valid'] is True
    assert result['data'] == 4

    # Valor inválido (no positivo)
    with pytest.raises(ValidationError):
        asyncio.run(chain.validate(-2))

    # Valor inválido (impar)
    with pytest.raises(ValidationError):
        asyncio.run(chain.validate(3))


def test_validator_chain_non_strict():
    """Test cadena no estricta"""
    chain = ValidatorChain(strict=False)

    def always_fail(value):
        raise ValueError("Always fails")

    chain.add_validator(always_fail, "failing")

    result = asyncio.run(chain.validate(5))
    assert result['valid'] is False
    assert len(result['warnings']) > 0
    assert result['data'] == 5  # Data se devuelve igual


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
