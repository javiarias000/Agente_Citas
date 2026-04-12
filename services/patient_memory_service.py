"""
Servicio de memoria estructurada y tipada por paciente.

Inspirado en el sistema de memoria de Claude Code:
- Tipos: user / feedback / project / reference
- Índice compacto siempre disponible (como MEMORY.md)
- Cuerpo completo cargado cuando es relevante

Uso:
    svc = PatientMemoryService(session)
    await svc.upsert(phone, "user", "alergia_penicilina",
                     "Alérgico a la penicilina", "Mencionado el 2026-04-12. ¿Por qué: reacción alérgica severa.")
    profile = await svc.load_profile(phone)   # siempre en contexto
    all_mem = await svc.load_all(phone)        # agrupado por tipo
"""

from __future__ import annotations

from typing import Dict, List, Literal, Optional

import structlog
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

logger = structlog.get_logger("patient_memory")

MemoryType = Literal["user", "feedback", "project", "reference"]

# Descripciones de tipos para el LLM
TYPE_LABELS = {
    "user":      "Perfil del paciente",
    "feedback":  "Preferencias y correcciones",
    "project":   "Tratamientos en curso",
    "reference": "Referencias externas",
}


class PatientMemoryService:
    """
    CRUD de patient_memories con interface tipada.

    Methods:
        upsert()       → crear o actualizar memoria por nombre
        load_profile() → cargar solo tipo 'user' (siempre en contexto)
        load_all()     → todas las memorias agrupadas por tipo
        delete()       → eliminar memoria por nombre
        format_index() → índice compacto (una línea por entrada)
        format_full()  → contenido completo agrupado por tipo
    """

    def __init__(self, session: AsyncSession):
        self._session = session

    async def upsert(
        self,
        phone: str,
        type: MemoryType,
        name: str,
        description: str,
        body: str,
    ) -> None:
        """
        Crea o actualiza una memoria. Upsert por (phone, name).

        Args:
            phone:       Número normalizado del paciente.
            type:        'user' | 'feedback' | 'project' | 'reference'
            name:        Identificador corto único (ej: "alergia_penicilina").
            description: Una línea descriptiva para el índice.
            body:        Contenido completo. Para feedback/project: incluir
                         "Por qué:" y "Cómo aplicar:".
        """
        await self._session.execute(
            text("""
                INSERT INTO patient_memories (phone, type, name, description, body)
                VALUES (:phone, :type, :name, :description, :body)
                ON CONFLICT (phone, name) DO UPDATE SET
                    type        = EXCLUDED.type,
                    description = EXCLUDED.description,
                    body        = EXCLUDED.body,
                    updated_at  = NOW()
            """),
            {"phone": phone, "type": type, "name": name,
             "description": description, "body": body},
        )
        await self._session.commit()
        logger.info("patient_memory upserted", phone=phone, type=type, name=name)

    async def delete(self, phone: str, name: str) -> bool:
        """Elimina memoria por nombre. Retorna True si existía."""
        result = await self._session.execute(
            text("DELETE FROM patient_memories WHERE phone=:phone AND name=:name"),
            {"phone": phone, "name": name},
        )
        await self._session.commit()
        return result.rowcount > 0

    async def load_profile(self, phone: str) -> List[Dict]:
        """
        Carga memorias tipo 'user' — siempre incluidas en contexto.
        Equivalente a MEMORY.md de Claude Code.
        """
        result = await self._session.execute(
            text("""
                SELECT name, description, body, updated_at
                FROM patient_memories
                WHERE phone=:phone AND type='user'
                ORDER BY updated_at DESC
            """),
            {"phone": phone},
        )
        return [dict(r._mapping) for r in result]

    async def load_all(self, phone: str) -> Dict[str, List[Dict]]:
        """Carga todas las memorias agrupadas por tipo."""
        result = await self._session.execute(
            text("""
                SELECT type, name, description, body, updated_at
                FROM patient_memories
                WHERE phone=:phone
                ORDER BY type, updated_at DESC
            """),
            {"phone": phone},
        )
        grouped: Dict[str, List[Dict]] = {t: [] for t in TYPE_LABELS}
        for row in result:
            r = dict(row._mapping)
            grouped[r["type"]].append(r)
        return grouped

    # ── Formateo ──────────────────────────────────────────────────────────────

    @staticmethod
    def format_profile(memories: List[Dict]) -> str:
        """
        Formatea memorias tipo 'user' como índice compacto (siempre en contexto).
        Ejemplo:
            PERFIL DEL PACIENTE:
            - alergia_penicilina: Alérgico a la penicilina
            - prefiere_mananas: Prefiere citas antes de las 12:00
        """
        if not memories:
            return ""
        lines = ["PERFIL DEL PACIENTE (datos permanentes — siempre aplicar):"]
        for m in memories:
            lines.append(f"  - {m['name']}: {m['description']}")
        return "\n".join(lines)

    @staticmethod
    def format_full(grouped: Dict[str, List[Dict]], include_types: Optional[List[str]] = None) -> str:
        """
        Formatea todas las memorias (o subset de tipos) con cuerpo completo.

        Args:
            include_types: Lista de tipos a incluir. None = todos.
        """
        sections = []
        for type_key, label in TYPE_LABELS.items():
            if include_types and type_key not in include_types:
                continue
            items = grouped.get(type_key, [])
            if not items:
                continue
            lines = [f"### {label}"]
            for m in items:
                lines.append(f"**{m['name']}** — {m['description']}")
                lines.append(m["body"])
            sections.append("\n".join(lines))
        return "\n\n".join(sections)
