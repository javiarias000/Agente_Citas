#!/usr/bin/env python3
"""
Prueba directa de cancelar_cita
"""

import asyncio
import sys
from pathlib import Path

# Añadir raíz al path
sys.path.insert(0, str(Path(__file__).parent))

from db import get_async_session
from services.appointment_service import AppointmentService

async def test_cancel():
    # ID de la cita que agendamos anteriormente
    appointment_id = "856f3b54-5883-491c-91b5-3c1e36ee5e73"

    async with get_async_session() as session:
        service = AppointmentService()
        success, message = await service.cancel_appointment(
            session=session,
            appointment_id=appointment_id
        )

        print(f"Cancelación: {'✅ Éxito' if success else '❌ Falló'}")
        print(f"Mensaje: {message}")

        # Verificar que la cita fue cancelada
        if success:
            from db.models import Appointment
            from sqlalchemy import select
            result = await session.execute(
                select(Appointment).where(Appointment.id == appointment_id)
            )
            appt = result.scalar_one_or_none()
            if appt:
                print(f"Estado actual: {appt.status}")

if __name__ == "__main__":
    asyncio.run(test_cancel())
