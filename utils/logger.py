# -*- coding: utf-8 -*-
"""
Configuración de logging estructurado para Arcadium Automation
"""

import logging
import sys
from pathlib import Path
from typing import Optional
import structlog
from core.config import get_settings


def setup_logger(log_level: str = None, log_dir: str = "/home/jav/arcadium_automation/logs") -> logging.Logger:
    """
    Configura logger estructurado con rotación de archivos

    Args:
        log_level: Nivel de logging (DEBUG, INFO, WARNING, ERROR)
        log_dir: Directorio para logs

    Returns:
        Logger configurado
    """
    log_level = log_level or get_settings().LOG_LEVEL
    log_dir_path = Path(log_dir)
    log_dir_path.mkdir(parents=True, exist_ok=True)

    # Configuración de structlog
    structlog.configure(
        processors=[
            structlog.stdlib.filter_by_level,
            structlog.stdlib.add_logger_name,
            structlog.stdlib.add_log_level,
            structlog.stdlib.PositionalArgumentsFormatter(),
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            structlog.processors.UnicodeDecoder(),
            structlog.processors.JSONRenderer()
        ],
        context_class=dict,
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )

    # Logger raíz
    root_logger = logging.getLogger()
    root_logger.setLevel(getattr(logging, log_level.upper()))

    # Limpiar handlers existentes
    root_logger.handlers.clear()

    # Handler para consola
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(logging.DEBUG)
    console_formatter = logging.Formatter(
        '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )
    console_handler.setFormatter(console_formatter)
    root_logger.addHandler(console_handler)

    # Handler para archivo (rotación diaria)
    from logging.handlers import TimedRotatingFileHandler
    file_handler = TimedRotatingFileHandler(
        filename=log_dir_path / "arcadium_automation.log",
        when="midnight",
        interval=1,
        backupCount=30,
        encoding="utf-8"
    )
    file_handler.setLevel(logging.DEBUG)
    file_formatter = structlog.stdlib.ProcessorFormatter(
        processor=structlog.processors.JSONRenderer(),
        foreign_pre_chain=[
            structlog.stdlib.add_logger_name,
            structlog.stdlib.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
        ],
    )
    file_handler.setFormatter(file_formatter)
    root_logger.addHandler(file_handler)

    # Logger específico
    logger = structlog.get_logger("arcadium")
    logger.info("Logger configurado", log_level=log_level, log_dir=str(log_dir_path))

    return logger


def get_chain_logger(chain_name: str) -> structlog.BoundLogger:
    """Obtiene logger para una cadena específica"""
    return structlog.get_logger(f"chain.{chain_name}")


def get_workflow_logger(workflow_id: str) -> structlog.BoundLogger:
    """Obtiene logger para un workflow específico"""
    return structlog.get_logger(f"workflow.{workflow_id}")


def get_module_logger(module_name: str) -> structlog.BoundLogger:
    """Obtiene logger para un módulo"""
    return structlog.get_logger(f"module.{module_name}")
