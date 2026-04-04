# Validadores para datos de Arcadium Garantiza integridad del 100% antes del procesamiento

import re
from typing import Any, Dict, List, Optional, Callable
from dataclasses import dataclass
from pydantic import BaseModel, Field, validator, EmailStr, HttpUrl
import jsonschema
from core.exceptions import ValidationError


# Esquemas Pydantic para validación
class PhoneNumber(BaseModel):
    """Modelo para número telefónico"""
    number: str = Field(..., min_length=10, max_length=15)
    country_code: Optional[str] = Field(None, min_length=1, max_length=5)

    @validator('number')
    def only_digits(cls, v):
        if not v.isdigit():
            raise ValueError('Número debe contener solo dígitos')
        return v

    def full_number(self) -> str:
        """Número completo con código de país"""
        if self.country_code:
            return f"{self.country_code}{self.number}"
        return self.number


class Message(BaseModel):
    """Modelo para mensaje de chat"""
    content: str = Field(..., min_length=1, max_length=4000)
    message_type: str = Field(..., pattern=r'^(text|audio|image|video|file)$')
    attachments: List[Dict[str, Any]] = Field(default_factory=list)
    timestamp: Optional[float] = None
    sender_id: Optional[str] = None

    @validator('content')
    def no_empty_content(cls, v):
        if not v.strip():
            raise ValueError('Contenido no puede estar vacío')
        return v.strip()

    @validator('attachments')
    def validate_attachments(cls, v):
        for att in v:
            if 'file_type' not in att:
                raise ValueError('Cada attachment debe tener file_type')
        return v


class Conversation(BaseModel):
    """Modelo para conversación"""
    conversation_id: int = Field(..., gt=0)
    account_id: int = Field(..., gt=0)
    phone: str = Field(..., min_length=10, max_length=20)
    user_name: Optional[str] = Field(None, max_length=200)
    messages: List[Message] = Field(default_factory=list)
    metadata: Dict[str, Any] = Field(default_factory=dict)

    @validator('phone')
    def validate_phone(cls, v):
        # Limpiar formato
        cleaned = re.sub(r'[^\d+]', '', v)
        if len(cleaned) < 10:
            raise ValueError('Número de teléfono inválido')
        return cleaned


class WebhookPayload(BaseModel):
    """Modelo para payload de webhook n8n"""
    body: Optional[Dict[str, Any]] = None
    chat: Optional[str] = None
    telefono: Optional[str] = None
    conversation_id: Optional[int] = None
    account_id: Optional[int] = None
    user_name: Optional[str] = None
    message_type: Optional[str] = None
    attachments: List[Dict[str, Any]] = Field(default_factory=list)

    def extract_conversation(self) -> Conversation:
        """Extrae conversación del payload"""
        # Determinar teléfono
        phone = self.telefono or self._extract_phone_from_body()

        # Determinar conversation_id
        conv_id = self.conversation_id or self._extract_id_from_body('conversation_id')

        # Extraer mensaje
        message_text = self.chat or self._extract_message_from_body()

        # Determinar tipo
        msg_type = self.message_type or self._detect_message_type()

        # Extraer user_name desde payload si no se proporcionó
        user_name = self.user_name or self._extract_user_name_from_body()

        # Crear conversación
        conversation = Conversation(
            conversation_id=conv_id or 0,
            account_id=self.account_id or 0,
            phone=phone,
            user_name=user_name,
            messages=[
                Message(
                    content=message_text,
                    message_type=msg_type,
                    attachments=self.attachments
                )
            ]
        )

        return conversation

    def _extract_user_name_from_body(self) -> Optional[str]:
        """Extrae nombre de usuario del body si está presente"""
        if self.body:
            paths = [
                'conversation.messages[0].sender.name',
                'conversation.contact.name',
                'meta.sender.name',
                'sender.name'
            ]
            for path in paths:
                value = self._get_nested_value(self.body, path)
                if value and isinstance(value, str):
                    return value
        return None

    def _extract_phone_from_body(self) -> str:
        # Primero verificar si hay telefono directo en el payload (no en body)
        if self.telefono:
            return str(self.telefono)

        if self.body:
            # Rutas posibles para teléfono en Chatwoot
            paths = [
                'conversation.messages[0].sender.phone_number',
                'meta.sender.phone_number',
                'sender.phone_number',
                'conversation.sender.phone_number'
            ]
            for path in paths:
                value = self._get_nested_value(self.body, path)
                if value:
                    return str(value)
        raise ValueError("No se pudo extraer teléfono del payload")

    def _extract_message_from_body(self) -> str:
        if self.body:
            paths = [
                'conversation.messages[0].content',
                'message',
                'text',
                'content'
            ]
            for path in paths:
                value = self._get_nested_value(self.body, path)
                if value and isinstance(value, str):
                    return value
        raise ValueError("No se pudo extraer mensaje del payload")

    def _extract_id_from_body(self, key: str) -> Optional[int]:
        if self.body:
            # Búsqueda en diferentes lugares
            for section in ['conversation', 'meta', '']:
                if section:
                    lookup = f"{section}.{key}"
                else:
                    lookup = key
                value = self._get_nested_value(self.body, lookup)
                if value:
                    try:
                        return int(value)
                    except (ValueError, TypeError):
                        continue
        return None

    def _detect_message_type(self) -> str:
        """Detecta tipo de mensaje desde attachments o body"""
        if self.attachments:
            file_type = self.attachments[0].get('file_type', '')
            if file_type == 'audio':
                return 'audio'
            elif file_type == 'image':
                return 'image'
            elif file_type == 'video':
                return 'video'
            else:
                return 'file'
        return 'text'

    def _get_nested_value(self, obj: Dict[str, Any], path: str) -> Any:
        """Obtiene valor anidado por path tipo 'a.b.c' o 'a.b[0].c'"""
        # Convertir notación array a dict: conversation.messages[0].content -> conversation.messages.0.content
        import re
        path = re.sub(r'\[(\d+)\]', r'.\1', path)
        keys = path.split('.')
        current = obj
        for key in keys:
            if not key:
                continue
            if isinstance(current, dict) and key in current:
                current = current[key]
            elif isinstance(current, list) and key.isdigit():
                idx = int(key)
                if 0 <= idx < len(current):
                    current = current[idx]
                else:
                    return None
            else:
                return None
        return current


# Funciones validadoras genéricas
def validate_required_fields(data: Dict[str, Any], required: List[str]) -> None:
    """Valida que todos los campos requeridos estén presentes"""
    missing = [field for field in required if field not in data or data[field] is None]
    if missing:
        raise ValidationError(
            f"Campos requeridos faltantes: {missing}",
            field=", ".join(missing),
            value=data
        )


def validate_schema(data: Dict[str, Any], schema: Dict[str, Any]) -> None:
    """Valida contra esquema JSON"""
    try:
        jsonschema.validate(instance=data, schema=schema)
    except jsonschema.ValidationError as e:
        raise ValidationError(
            f"Validación de esquema falló: {e.message}",
            field=list(e.path) if e.path else None,
            value=data
        )


def validate_phone_number(phone: str, country_codes: List[str] = ['+34', '+52', '+1', '+57']) -> bool:
    """Valida número telefónico internacional"""
    if not phone:
        return False

    # Limpiar
    cleaned = re.sub(r'[^\d+]', '', phone)

    # Verificar código de país
    has_valid_prefix = any(cleaned.startswith(cc) for cc in country_codes)

    # Verificar longitud total
    # - Con prefijo: 10-15 caracteres (ej: +34612345678 = 13)
    # - Sin prefijo: 9 dígitos es válido (móvil español)
    length_ok = (has_valid_prefix and 10 <= len(cleaned) <= 15) or (cleaned.isdigit() and 9 <= len(cleaned) <= 15)

    return has_valid_prefix or (cleaned.isdigit() and length_ok)


def validate_message_content(content: str) -> bool:
    """Valida contenido de mensaje"""
    if not content or not content.strip():
        return False

    # Límite de longitud
    if len(content) > 4000:
        return False

    # Detectar solo espacios/saltos
    if not content.strip():
        return False

    return True


def sanitize_text(text: str, max_length: int = 4000) -> str:
    """Sanitiza texto para procesamiento"""
    if not text:
        return ""

    # Remover espacios extremos
    text = text.strip()

    # Reemplazar múltiples espacios por uno solo
    import re
    text = re.sub(r'\s+', ' ', text)

    # Limitar longitud
    if len(text) > max_length:
        text = text[:max_length-3] + "..."

    return text


class ValidatorChain:
    """Cadena de validadores que se ejecutan secuencialmente"""

    def __init__(self, strict: bool = True):
        self.validators: List[Callable] = []
        self.strict = strict

    def add_validator(self, validator: Callable, name: str) -> 'ValidatorChain':
        """Añade un validador a la cadena"""
        self.validators.append((name, validator))
        return self

    async def validate(self, data: Any) -> Dict[str, Any]:
        """
        Ejecuta todos los validadores
        Retorna datos validados o lanza ValidationError
        """
        current_data = data
        errors: List[Dict[str, Any]] = []

        for name, validator in self.validators:
            try:
                # Si el validador modifica datos, actualizar
                result = validator(current_data)
                if result is not None:
                    current_data = result
            except ValidationError as e:
                errors.append({
                    "validator": name,
                    "error": str(e),
                    "field": e.details.get("field")
                })

                if self.strict:
                    raise ValidationError(
                        f"Validación '{name}' falló",
                        details={"errors": errors}
                    )
            except Exception as e:
                errors.append({
                    "validator": name,
                    "error": str(e),
                    "unexpected": True
                })
                if self.strict:
                    raise ValidationError(
                        f"Error inesperado en validador '{name}'",
                        details={"errors": errors}
                    )

        if errors and not self.strict:
            return {
                "valid": False,
                "data": current_data,
                "warnings": errors
            }

        return {
            "valid": True,
            "data": current_data,
            "warnings": errors if errors else []
        }
