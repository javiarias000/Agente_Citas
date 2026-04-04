"monitor.py: Sistema de monitoreo y métricas para Arcadium"

import asyncio
from typing import Dict, Any, List, Optional
from datetime import datetime, timedelta
import psutil
import structlog
from dataclasses import dataclass, asdict
from core.landchain import LandChain
from prometheus_client import Counter, Gauge, Histogram, start_http_server

logger = structlog.get_logger("monitor")


# Métricas Prometheus
CHAINS_EXECUTED = Counter(
    'arcadium_chains_executed_total',
    'Total de cadenas ejecutadas',
    ['chain_name', 'status']
)
CHAINS_DURATION = Histogram(
    'arcadium_chains_duration_seconds',
    'Duración de cadenas',
    ['chain_name']
)
LINK_EXECUTIONS = Counter(
    'arcadium_links_executed_total',
    'Total de eslabones ejecutados',
    ['chain_name', 'link_name', 'status']
)
SYSTEM_CPU = Gauge('arcadium_system_cpu_percent', 'Uso de CPU %')
SYSTEM_MEMORY = Gauge('arcadium_system_memory_percent', 'Uso de memoria %')
ACTIVE_CHAINS = Gauge('arcadium_active_chains', 'Cadenas activas')
QUEUE_SIZE = Gauge('arcadium_queue_size', 'Tamaño de cola pendiente')


@dataclass
class SystemMetrics:
    """Métricas del sistema"""
    cpu_percent: float
    memory_percent: float
    disk_usage_percent: float
    network_io: Dict[str, int]
    timestamp: datetime

    @classmethod
    async def capture(cls) -> 'SystemMetrics':
        """Captura métricas actuales del sistema"""
        cpu = psutil.cpu_percent(interval=0.1)
        memory = psutil.virtual_memory().percent
        disk = psutil.disk_usage('/').percent
        net = psutil.net_io_counters()

        return cls(
            cpu_percent=cpu,
            memory_percent=memory,
            disk_usage_percent=disk,
            network_io={
                'bytes_sent': net.bytes_sent,
                'bytes_recv': net.bytes_recv
            },
            timestamp=datetime.utcnow()
        )


@dataclass
class ChainMetrics:
    """Métricas de ejecución de cadena"""
    chain_name: str
    execution_time_ms: float
    status: str
    links_total: int
    links_success: int
    links_failed: int
    retries_total: int
    timestamp: datetime
    metadata: Dict[str, Any]


class MetricsCollector:
    """Colector de métricas del sistema"""

    def __init__(self, enable_prometheus: bool = True):
        self.enable_prometheus = enable_prometheus
        self._chain_history: List[ChainMetrics] = []
        self._system_history: List[SystemMetrics] = []
        self._max_history = 1000

        if enable_prometheus:
            start_http_server(9090)
            logger.info("Métricas Prometheus iniciadas en puerto 9090")

    async def record_chain_execution(self, chain_result: Dict[str, Any]):
        """Registra ejecución de cadena"""
        metrics = ChainMetrics(
            chain_name=chain_result['chain_name'],
            execution_time_ms=chain_result['total_time_ms'],
            status=chain_result['status'],
            links_total=chain_result['total_links'],
            links_success=chain_result['successful_links'],
            links_failed=chain_result['failed_links'],
            retries_total=sum(r.get('retry_count', 0) for r in chain_result.get('results', [])),
            timestamp=datetime.utcnow(),
            metadata={
                "final_data_keys": list(chain_result.get('final_data', {}).keys())
            }
        )

        self._chain_history.append(metrics)
        if len(self._chain_history) > self._max_history:
            self._chain_history = self._chain_history[-self._max_history:]

        # Actualizar Prometheus
        if self.enable_prometheus:
            CHAINS_EXECUTED.labels(
                chain_name=metrics.chain_name,
                status=metrics.status
            ).inc()
            CHAINS_DURATION.labels(
                chain_name=metrics.chain_name
            ).observe(metrics.execution_time_ms / 1000)

        logger.info(
            "Métrica registrada",
            chain=metrics.chain_name,
            status=metrics.status,
            time_ms=metrics.execution_time_ms
        )

    async def record_link_execution(self, chain_name: str, link_name: str, status: str):
        """Registra ejecución de eslabón"""
        if self.enable_prometheus:
            LINK_EXECUTIONS.labels(
                chain_name=chain_name,
                link_name=link_name,
                status=status
            ).inc()

    async def collect_system_metrics(self) -> SystemMetrics:
        """Captura métricas del sistema"""
        metrics = await SystemMetrics.capture()
        self._system_history.append(metrics)

        if len(self._system_history) > self._max_history:
            self._system_history = self._system_history[-self._max_history:]

        # Actualizar Prometheus
        if self.enable_prometheus:
            SYSTEM_CPU.set(metrics.cpu_percent)
            SYSTEM_MEMORY.set(metrics.memory_percent)

        return metrics

    def get_chain_stats(self, chain_name: str, last_hours: int = 24) -> Dict[str, Any]:
        """Obtiene estadísticas de cadena"""

        cutoff = datetime.utcnow() - timedelta(hours=last_hours)
        recent = [m for m in self._chain_history if m.timestamp >= cutoff and m.chain_name == chain_name]

        if not recent:
            return {"error": "No hay datos"}

        total = len(recent)
        successful = sum(1 for m in recent if m.status == 'success')
        failed = sum(1 for m in recent if m.status == 'failed')
        avg_time = sum(m.execution_time_ms for m in recent) / total if total > 0 else 0

        return {
            "chain_name": chain_name,
            "period_hours": last_hours,
            "total_executions": total,
            "successful": successful,
            "failed": failed,
            "success_rate": (successful / total * 100) if total > 0 else 0,
            "avg_time_ms": avg_time,
            "last_execution": recent[-1].timestamp.isoformat() if recent else None
        }

    def get_system_health(self) -> Dict[str, Any]:
        """Obtiene estado de salud del sistema"""
        if not self._system_history:
            # Synchronous capture since this is not async
            import psutil
            cpu = psutil.cpu_percent(interval=0.1)
            memory = psutil.virtual_memory().percent
            disk = psutil.disk_usage('/').percent
            latest = type('obj', (object,), {
                'cpu_percent': cpu,
                'memory_percent': memory,
                'disk_usage_percent': disk,
                'timestamp': datetime.utcnow()
            })()
        else:
            latest = self._system_history[-1]

        # Evaluar salud
        issues = []
        if latest.cpu_percent > 90:
            issues.append("CPU al límite")
        if latest.memory_percent > 90:
            issues.append("Memoria al límite")
        if latest.disk_usage_percent > 95:
            issues.append("Disco casi lleno")

        health_status = "healthy" if not issues else "warning" if len(issues) < 2 else "critical"

        return {
            "status": health_status,
            "timestamp": latest.timestamp.isoformat(),
            "cpu_percent": latest.cpu_percent,
            "memory_percent": latest.memory_percent,
            "disk_usage_percent": latest.disk_usage_percent,
            "issues": issues,
            "uptime_seconds": psutil.boot_time()
        }

    async def start_background_collection(self, interval_seconds: int = 60):
        """Inicia recolección de métricas en background"""
        logger.info("Iniciando recolector métricas background", interval=interval_seconds)

        while True:
            try:
                await self.collect_system_metrics()
                await asyncio.sleep(interval_seconds)
            except asyncio.CancelledError:
                logger.info("Recolector métricas detenido")
                break
            except Exception as e:
                logger.error("Error en recolector métricas", error=str(e))
                await asyncio.sleep(interval_seconds)


class HealthChecker:
    """Verificador de salud del sistema"""

    def __init__(self, chains: Dict[str, LandChain], check_interval: int = 30):
        self.chains = chains
        self.check_interval = check_interval
        self._last_check: Optional[datetime] = None
        self._status: Dict[str, Any] = {"status": "unknown"}
        self.logger = logger.bind(component="health_checker")

    async def check_all(self) -> Dict[str, Any]:
        """Ejecuta verificación completa de salud"""
        checks = {
            "timestamp": datetime.utcnow().isoformat(),
            "components": {},
            "overall": "healthy"
        }

        # Verificar cada cadena
        for name, chain in self.chains.items():
            metrics = chain.get_metrics()
            total = metrics.get('total_executions', 0)
            failed = metrics.get('failed_executions', 0)

            success_rate = 0 if total == 0 else ((total - failed) / total * 100)

            status = "healthy"
            if success_rate < 90 and total > 10:
                status = "warning"
            if success_rate < 70 and total > 10:
                status = "critical"

            checks["components"][name] = {
                "status": status,
                "total_executions": total,
                "failed_executions": failed,
                "success_rate": success_rate,
                "avg_time_ms": metrics.get('total_time_ms', 0) / total if total > 0 else 0
            }

            if status == "critical":
                checks["overall"] = "critical"
            elif status == "warning" and checks["overall"] == "healthy":
                checks["overall"] = "warning"

        # Verificar recursos
        import psutil
        cpu_percent = psutil.cpu_percent(interval=0.5)
        mem_percent = psutil.virtual_memory().percent

        checks["resources"] = {
            "cpu_percent": cpu_percent,
            "memory_percent": mem_percent,
            "healthy": cpu_percent < 85 and mem_percent < 85
        }

        if cpu_percent >= 95 or mem_percent >= 95:
            checks["overall"] = "critical"

        self._status = checks
        self._last_check = datetime.utcnow()

        return checks

    async def start_monitoring(self):
        """Inicia monitoreo continuo"""
        self.logger.info("Iniciando monitoreo continuo", interval=self.check_interval)

        while True:
            try:
                await self.check_all()
                await asyncio.sleep(self.check_interval)
            except asyncio.CancelledError:
                self.logger.info("Monitoreo detenido")
                break
            except Exception as e:
                self.logger.error("Error en monitoreo", error=str(e))
                await asyncio.sleep(self.check_interval)

    def get_status(self) -> Dict[str, Any]:
        """Retorna último estado"""
        return self._status.copy()
