# -*- coding: utf-8 -*-
"""
Sistema de cadenas de procesamiento (Landchain)
Garantiza ejecución secuencial con validación en cada eslabón
"""

import asyncio
import time
from typing import Any, Callable, Dict, List, Optional, Union
from dataclasses import dataclass, field
from enum import Enum
import logging
from functools import wraps

from .exceptions import ChainError, ChainTimeoutError, ChainValidationError


class ChainStatus(Enum):
    """Estado de una cadena"""
    PENDING = "pending"
    RUNNING = "running"
    SUCCESS = "success"
    FAILED = "failed"
    RETRYING = "retrying"
    TIMEOUT = "timeout"
    SKIPPED = "skipped"


@dataclass
class ChainResult:
    """Resultado de ejecución de un eslabón"""
    status: ChainStatus
    data: Any
    error: Optional[Exception] = None
    metadata: Dict[str, Any] = field(default_factory=dict)
    execution_time_ms: float = 0.0
    retry_count: int = 0
    timestamp: float = field(default_factory=time.time)

    def to_dict(self) -> Dict[str, Any]:
        error_str = None
        if self.error:
            msg = str(self.error)
            if msg:
                error_str = msg
            else:
                # Si el mensaje está vacío, usar el nombre de la clase de la excepción
                error_str = type(self.error).__name__
        return {
            "status": self.status.value,
            "data": self.data,
            "error": error_str,
            "metadata": self.metadata,
            "execution_time_ms": self.execution_time_ms,
            "retry_count": self.retry_count,
            "timestamp": self.timestamp
        }


@dataclass
class ChainLink:
    """Eslabón individual de la cadena"""
    name: str
    func: Callable
    validator: Optional[Callable] = None
    retry_on: tuple = (Exception,)
    max_retries: int = 3
    retry_delay: float = 1.0
    timeout: Optional[float] = None
    rollback_on_failure: bool = False
    rollback_func: Optional[Callable] = None
    continue_on_failure: bool = False
    metadata: Dict[str, Any] = field(default_factory=dict)

    async def execute(self, data: Any, context: Dict[str, Any]) -> ChainResult:
        """Ejecuta el eslabón con validación y reintentos"""
        start_time = time.time()

        # Validación previa
        if self.validator:
            try:
                self.validator(data)
            except Exception as e:
                return ChainResult(
                    status=ChainStatus.FAILED,
                    data=data,
                    error=ChainValidationError(f"Validación falló en {self.name}: {str(e)}"),
                    metadata={"validation_error": str(e)}
                )

        # Ejecución con reintentos
        retry_count = 0
        last_error = None

        while retry_count <= self.max_retries:
            try:
                # Ejecutar función (soporta sync y async)
                if self.timeout:
                    result = await self._execute_with_timeout(data, context, self.timeout)
                else:
                    result = await self._execute_function(data, context)

                execution_time = (time.time() - start_time) * 1000

                return ChainResult(
                    status=ChainStatus.SUCCESS,
                    data=result,
                    execution_time_ms=execution_time,
                    retry_count=retry_count,
                    metadata=self.metadata
                )

            except self.retry_on as e:
                last_error = e
                retry_count += 1

                if retry_count <= self.max_retries:
                    await asyncio.sleep(self.retry_delay * (2 ** (retry_count - 1)))  # Exponential backoff
                    continue
                else:
                    execution_time = (time.time() - start_time) * 1000
                    return ChainResult(
                        status=ChainStatus.FAILED,
                        data=data,
                        error=last_error,
                        execution_time_ms=execution_time,
                        retry_count=retry_count,
                        metadata={"max_retries_exceeded": True}
                    )
            except Exception as e:
                execution_time = (time.time() - start_time) * 1000
                return ChainResult(
                    status=ChainStatus.FAILED,
                    data=data,
                    error=e,
                    execution_time_ms=execution_time,
                    retry_count=retry_count,
                    metadata={"unexpected_error": True}
                )

    async def _execute_function(self, data: Any, context: Dict[str, Any]) -> Any:
        """Ejecuta la función del link, manejando sync y async"""
        if asyncio.iscoroutinefunction(self.func):
            return await self.func(data, context)
        else:
            # Función síncrona: ejecutar en executor
            loop = asyncio.get_event_loop()
            return await loop.run_in_executor(None, lambda: self.func(data, context))

    async def _execute_with_timeout(self, data: Any, context: Dict[str, Any], timeout: float) -> Any:
        """Ejecuta la función con timeout, manejando sync y async"""
        if asyncio.iscoroutinefunction(self.func):
            return await asyncio.wait_for(self.func(data, context), timeout=timeout)
        else:
            loop = asyncio.get_event_loop()
            return await asyncio.wait_for(
                loop.run_in_executor(None, lambda: self.func(data, context)),
                timeout=timeout
            )


class LandChain:
    """
    Cadena de procesamiento principal
    Ejecuta secuencias de operaciones con garantía de éxito
    """

    def __init__(
        self,
        name: str,
        max_retries: int = 3,
        timeout: float = 300.0,
        strict_mode: bool = True,
        logger: Optional[logging.Logger] = None
    ):
        self.name = name
        self.links: List[ChainLink] = []
        self.max_retries = max_retries
        self.timeout = timeout
        self.strict_mode = strict_mode
        self.logger = logger or logging.getLogger(f"landchain.{name}")
        self.context: Dict[str, Any] = {}
        self._metrics: Dict[str, Any] = {
            "total_executions": 0,
            "successful_executions": 0,
            "failed_executions": 0,
            "total_time_ms": 0.0,
            "link_metrics": {}
        }

    def add_link(
        self,
        name: str,
        func: Callable,
        validator: Optional[Callable] = None,
        **kwargs
    ) -> 'LandChain':
        """Añade un eslabón a la cadena"""
        link = ChainLink(
            name=name,
            func=func,
            validator=validator,
            max_retries=kwargs.get('max_retries', self.max_retries),
            retry_delay=kwargs.get('retry_delay', 1.0),
            timeout=kwargs.get('timeout', self.timeout),
            rollback_on_failure=kwargs.get('rollback_on_failure', False),
            rollback_func=kwargs.get('rollback_func'),
            continue_on_failure=kwargs.get('continue_on_failure', False),
            metadata=kwargs.get('metadata', {})
        )
        self.links.append(link)
        return self

    def set_context(self, **kwargs) -> 'LandChain':
        """Establece contexto global de la cadena"""
        self.context.update(kwargs)
        return self

    async def execute(self, initial_data: Any) -> Dict[str, Any]:
        """
        Ejecuta la cadena completa
        Retorna resultado consolidado con métricas
        """
        self.logger.info(f"Iniciando cadena '{self.name}' con {len(self.links)} eslabones")
        start_time = time.time()

        results: List[ChainResult] = []
        current_data = initial_data
        chain_failed = False

        try:
            # Ejecutar cada eslabón
            for i, link in enumerate(self.links):
                self.logger.info(f"Ejecutando eslabón {i+1}/{len(self.links)}: {link.name}")

                result = await link.execute(current_data, self.context)
                results.append(result)

                # Actualizar métricas del eslabón
                if link.name not in self._metrics["link_metrics"]:
                    self._metrics["link_metrics"][link.name] = {
                        "executions": 0,
                        "successes": 0,
                        "failures": 0,
                        "total_time_ms": 0.0
                    }

                metrics = self._metrics["link_metrics"][link.name]
                metrics["executions"] += 1
                metrics["total_time_ms"] += result.execution_time_ms

                if result.status == ChainStatus.SUCCESS:
                    metrics["successes"] += 1
                    current_data = result.data
                    self.logger.info(f"✓ Eslabón {link.name} completado en {result.execution_time_ms:.2f}ms")
                else:
                    metrics["failures"] += 1
                    # Determinar si este fallo es crítico para el estado final
                    # Es crítico si: no se permite continuar (continue_on_failure=False) O la cadena es estricta
                    is_critical = not link.continue_on_failure or self.strict_mode
                    if is_critical:
                        chain_failed = True

                    # Rollback si está configurado
                    if link.rollback_on_failure and link.rollback_func:
                        try:
                            self.logger.warning(f"Ejecutando rollback para {link.name}")
                            await self._execute_rollback(link.rollback_func, current_data, self.context)
                        except Exception as e:
                            self.logger.error(f"Rollback falló: {e}")

                    # Si falla y no debe continuar, detener cadena
                    if not link.continue_on_failure and self.strict_mode:
                        self.logger.error(f"Cadena detenida en eslabón '{link.name}': {result.error}")
                        break
                    else:
                        self.logger.warning(f"Eslabón '{link.name}' falló pero continuando")

            # Resumen final
            total_time = (time.time() - start_time) * 1000
            self._metrics["total_executions"] += 1
            self._metrics["total_time_ms"] += total_time

            final_status = ChainStatus.SUCCESS if not chain_failed else ChainStatus.FAILED
            if chain_failed:
                self._metrics["failed_executions"] += 1
            else:
                self._metrics["successful_executions"] += 1

            summary = {
                "chain_name": self.name,
                "status": final_status.value,
                "total_time_ms": total_time,
                "total_links": len(self.links),
                "executed_links": len(results),
                "successful_links": sum(1 for r in results if r.status == ChainStatus.SUCCESS),
                "failed_links": sum(1 for r in results if r.status == ChainStatus.FAILED),
                "results": [r.to_dict() for r in results],
                "final_data": current_data,
                "metrics": self._metrics
            }

            self.logger.info(
                f"Cadena '{self.name}' completada: {final_status.value} "
                f"({summary['successful_links']}/{len(results)} eslabones exitosos)"
            )

            return summary

        except asyncio.TimeoutError as e:
            self.logger.error(f"Timeout en cadena '{self.name}': {e}")
            self._metrics["failed_executions"] += 1
            raise ChainTimeoutError(f"Timeout después de {self.timeout}s")

        except Exception as e:
            self.logger.error(f"Error inesperado en cadena '{self.name}': {e}")
            self._metrics["failed_executions"] += 1
            raise ChainError(f"Error en cadena: {e}")

    async def _execute_rollback(self, rollback_func: Callable, data: Any, context: Dict[str, Any]) -> Any:
        """Ejecuta rollback, soportando funciones sync y async"""
        if asyncio.iscoroutinefunction(rollback_func):
            return await rollback_func(data, context)
        else:
            loop = asyncio.get_event_loop()
            return await loop.run_in_executor(None, lambda: rollback_func(data, context))

    def get_metrics(self) -> Dict[str, Any]:
        """Retorna métricas de la cadena"""
        return self._metrics.copy()

    def reset_metrics(self) -> None:
        """Resetea métricas"""
        self._metrics = {
            "total_executions": 0,
            "successful_executions": 0,
            "failed_executions": 0,
            "total_time_ms": 0.0,
            "link_metrics": {}
        }


def retryable(max_retries: int = 3, delay: float = 1.0, exceptions: tuple = (Exception,)):
    """
    Decorador para funciones retryables
    Uso: @retryable(max_retries=3, delay=2.0)
    """
    def decorator(func):
        @wraps(func)
        async def wrapper(*args, **kwargs):
            retry_count = 0
            last_error = None

            while retry_count <= max_retries:
                try:
                    return await func(*args, **kwargs)
                except exceptions as e:
                    last_error = e
                    retry_count += 1

                    if retry_count <= max_retries:
                        await asyncio.sleep(delay * (2 ** (retry_count - 1)))
                        continue
                    else:
                        raise last_error

            return None
        return wrapper
    return decorator
