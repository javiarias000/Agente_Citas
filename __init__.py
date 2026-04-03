# Arcadium Automation - Paquete principal
"""
Sistema de automatización sin n8n - Arcadium
Arquitectura limpia con FastAPI, LangChain y PostgreSQL
"""

# Configuración
from .core.config import get_settings, Settings

# API FastAPI
from .core.orchestrator import ArcadiumAPI, create_app

# Alias para compatibilidad con código existente
ArcadiumAutomation = ArcadiumAPI

# LandChain (aún disponible si se necesita)
from .core.landchain import LandChain, ChainLink, ChainResult, ChainStatus

# State management (si se necesita aparte)
from .core.state import StateManager, StateStorage, MemoryStorage, StateKeys

# Agentes
from .agents.deyy_agent import DeyyAgent

# Servicios
from .services.whatsapp_service import WhatsAppService, WhatsAppMessage, WhatsAppError
from .services.appointment_service import AppointmentService, TimeSlot

# Memoria
from .memory.memory_manager import MemoryManager, BaseMemory

# DB models
from .db.models import Conversation, Message, Appointment, ToolCallLog

__all__ = [
    # Config
    'get_settings',
    'Settings',
    # API
    'ArcadiumAPI',
    'ArcadiumAutomation',
    'create_app',
    # Agents
    'DeyyAgent',
    # Services
    'WhatsAppService',
    'WhatsAppMessage',
    'WhatsAppError',
    'AppointmentService',
    'TimeSlot',
    # Memory
    'MemoryManager',
    'BaseMemory',
    # DB
    'Conversation',
    'Message',
    'Appointment',
    'ToolCallLog',
    # Chains (legacy)
    'LandChain',
    'ChainLink',
    'ChainResult',
    'ChainStatus',
    # State (legacy)
    'StateManager',
    'StateStorage',
    'MemoryStorage',
    'StateKeys'
]

__version__ = '2.0.0'
