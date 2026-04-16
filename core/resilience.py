#!/usr/bin/env python3
"""
Decoradores de resiliencia: timeout + retry + circuit breaker
Centraliza la configuración de reintentos y timeouts para funciones críticas.
"""

import asyncio
from functools import wraps
from typing import Callable, Any, TypeVar, Optional
import structlog
from tenacity import (
    retry,
    stop_after_attempt,
    wait_exponential,
    retry_if_exception_type,
    RetryError,
)

logger = structlog.get_logger("resilience")

F = TypeVar('F', bound=Callable[..., Any])


def with_timeout(seconds: float) -> Callable[[F], F]:
    """
    Decorador para aplicar timeout a funciones async.

    Uso:
        @with_timeout(5.0)
        async def slow_operation():
            ...
    """
    def decorator(func: F) -> F:
        @wraps(func)
        async def wrapper(*args, **kwargs):
            try:
                return await asyncio.wait_for(
                    func(*args, **kwargs),
                    timeout=seconds
                )
            except asyncio.TimeoutError:
                logger.error(
                    "Function timeout",
                    function=func.__name__,
                    timeout_seconds=seconds
                )
                raise TimeoutError(f"{func.__name__} exceeded {seconds}s timeout")
        return wrapper  # type: ignore
    return decorator


def with_retry(
    max_attempts: int = 3,
    wait_seconds: float = 1.0,
    exception_types: tuple = (Exception,)
) -> Callable[[F], F]:
    """
    Decorador para reintentos exponenciales.

    Uso:
        @with_retry(max_attempts=3, wait_seconds=2.0, exception_types=(IOError, TimeoutError))
        async def flaky_operation():
            ...
    """
    def decorator(func: F) -> F:
        retry_decorator = retry(
            stop=stop_after_attempt(max_attempts),
            wait=wait_exponential(multiplier=wait_seconds, min=wait_seconds, max=wait_seconds * 10),
            retry=retry_if_exception_type(exception_types),
            reraise=True
        )

        @wraps(func)
        async def wrapper(*args, **kwargs):
            async def _run():
                return await func(*args, **kwargs)

            # Crear versión sync wrapper para tenacity
            attempt = 0
            last_exception = None

            while attempt < max_attempts:
                try:
                    result = await func(*args, **kwargs)
                    return result
                except exception_types as e:
                    attempt += 1
                    last_exception = e
                    if attempt < max_attempts:
                        wait_time = wait_seconds * (2 ** (attempt - 1))
                        wait_time = min(wait_time, wait_seconds * 10)
                        logger.warning(
                            "Retry attempt",
                            function=func.__name__,
                            attempt=attempt,
                            max_attempts=max_attempts,
                            error=str(e),
                            wait_seconds=wait_time
                        )
                        await asyncio.sleep(wait_time)
                    else:
                        logger.error(
                            "All retry attempts exhausted",
                            function=func.__name__,
                            max_attempts=max_attempts
                        )
                        raise
                except Exception as e:
                    # Exception no en la lista — no reintentar
                    logger.error(
                        "Non-retryable exception",
                        function=func.__name__,
                        error=str(e)
                    )
                    raise

            if last_exception:
                raise last_exception

        return wrapper  # type: ignore
    return decorator


def with_resilience(
    timeout_seconds: float = 30.0,
    max_retries: int = 3,
    retry_wait_seconds: float = 1.0,
    retry_on: tuple = (IOError, TimeoutError, ConnectionError)
) -> Callable[[F], F]:
    """
    Combina timeout + retry en un solo decorador.

    Uso:
        @with_resilience(
            timeout_seconds=10.0,
            max_retries=2,
            retry_wait_seconds=2.0
        )
        async def api_call():
            ...
    """
    def decorator(func: F) -> F:
        @with_retry(
            max_attempts=max_retries,
            wait_seconds=retry_wait_seconds,
            exception_types=retry_on
        )
        @with_timeout(timeout_seconds)
        @wraps(func)
        async def wrapper(*args, **kwargs):
            return await func(*args, **kwargs)
        return wrapper  # type: ignore
    return decorator
