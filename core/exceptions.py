"Excepciones personalizadas del sistema Arcadium Automation"

from typing import Optional, Dict, Any


class ArcadiumError(Exception):
    "Excepción base del sistema"
    def __init__(self, message: str, code: str = "ARC_ERROR", details: Optional[Dict[str, Any]] = None):
        self.code = code
        self.details = details or {}
        super().__init__(f"[{code}] {message}")


class ChainError(ArcadiumError):
    "Error en ejecución de cadena"
    def __init__(self, message: str, chain_name: Optional[str] = None, **kwargs):
        details = kwargs.get("details", {})
        if chain_name:
            details["chain_name"] = chain_name
        super().__init__(message, code="CHAIN_ERROR", details=details)


class ChainTimeoutError(ChainError):
    "Timeout en ejecución de cadena"
    def __init__(self, message: str, timeout: float, **kwargs):
        details = kwargs.get("details", {})
        details["timeout_seconds"] = timeout
        super().__init__(message, **details)


class ChainValidationError(ChainError):
    "Error de validación en eslabón"
    def __init__(self, message: str, validation_data: Optional[Dict[str, Any]] = None):
        super().__init__(message, code="VALIDATION_ERROR", details={"validation_data": validation_data})


class WorkflowError(ArcadiumError):
    "Error en workflow n8n"
    def __init__(self, message: str, workflow_id: Optional[str] = None, node_id: Optional[str] = None, **kwargs):
        details = kwargs.get("details", {})
        if workflow_id:
            details["workflow_id"] = workflow_id
        if node_id:
            details["node_id"] = node_id
        super().__init__(message, code="WORKFLOW_ERROR", details=details)


class APIError(ArcadiumError):
    "Error en llamada API"
    def __init__(self, message: str, status_code: Optional[int] = None, endpoint: Optional[str] = None, response: Optional[str] = None):
        details = {}
        if status_code:
            details["status_code"] = status_code
        if endpoint:
            details["endpoint"] = endpoint
        if response:
            details["response"] = response
        super().__init__(message, code="API_ERROR", details=details)


class ConfigurationError(ArcadiumError):
    "Error de configuración"
    def __init__(self, message: str, config_key: Optional[str] = None):
        details = {"config_key": config_key} if config_key else {}
        super().__init__(message, code="CONFIG_ERROR", details=details)


class StateError(ArcadiumError):
    "Error en gestión de estado"
    def __init__(self, message: str, state_key: Optional[str] = None, operation: Optional[str] = None):
        details = {}
        if state_key:
            details["state_key"] = state_key
        if operation:
            details["operation"] = operation
        super().__init__(message, code="STATE_ERROR", details=details)


class ValidationError(ArcadiumError):
    """Error de validación de datos"""
    def __init__(self, message: str, field: Optional[str] = None, value: Optional[Any] = None, schema: Optional[str] = None):
        details = {}
        if field:
            details["field"] = field
        if value is not None:
            details["value"] = value
        if schema:
            details["schema"] = schema
        super().__init__(message, code="VALIDATION_ERROR", details=details)


class TranscriptionError(ArcadiumError):
    """Error en transcripción de audio"""
    def __init__(self, message: str, audio_source: Optional[str] = None, engine: str = "whisper"):
        details = {"audio_source": audio_source, "engine": engine}
        super().__init__(message, code="TRANSCRIPTION_ERROR", details=details)


class ConversationError(ArcadiumError):
    """Error en gestión de conversaciones"""
    def __init__(self, message: str, conversation_id: Optional[str] = None, phone: Optional[str] = None):
        details = {}
        if conversation_id:
            details["conversation_id"] = conversation_id
        if phone:
            details["phone"] = phone
        super().__init__(message, code="CONVERSATION_ERROR", details=details)
