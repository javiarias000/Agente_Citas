#!/usr/bin/env python3
"""
Utilidades para normalización de números de teléfono
Garantiza formato consistente E.164 para usar como session_id
"""

import re
from typing import Optional


def normalize_phone(phone: str, default_country_code: str = "+34") -> str:
    """
    Normaliza un número de teléfono a formato E.164 internacional.

    Reglas:
    1. Remueve espacios, guiones, paréntesis
    2. Si empieza con '+', lo mantiene (ya es internacional)
    3. Si empieza con '00', lo reemplaza por '+' (formato internacional alternativo)
    4. Si no tiene '+', asume código de país de default_country_code
    5. Elimina ceros iniciales después del código de país

    Ejemplos:
        "+34 612 345 678" → "+34612345678"
        "612345678" → "+34612345678"
        "34 612345678" → "+34612345678"
        "0034612345678" → "+34612345678"

    Args:
        phone: Número entrante (cualquier formato)
        default_country_code: Código de país por defecto (ej: "+34" para España)

    Returns:
        Número en formato E.164 (ej: "+34612345678")
    """
    if not phone:
        raise ValueError("Número de teléfono vacío")

    # Limpiar: solo mantener dígitos y signo +
    cleaned = re.sub(r'[^\d+]', '', phone)

    if not cleaned:
        raise ValueError(f"Número inválido después de limpieza: {phone}")

    # Manejar prefijo '00' (formato internacional alternativo)
    if cleaned.startswith('00'):
        # Remove leading zeros and add +
        cleaned = '+' + cleaned[2:]

    # Si ya tiene formato internacional (+ seguido de dígitos)
    if cleaned.startswith('+'):
        # Quitar ceros iniciales después del código de país
        # Ej: +34 012345678 → +3412345678 (el 0 sobra después del código de país)
        parts = re.match(r'^(\+\d{1,3})(0\d+)$', cleaned)
        if parts:
            country_code = parts.group(1)
            rest = parts.group(2)[1:]  # Quitar el 0 inicial
            cleaned = country_code + rest
        return cleaned

    # Si no tiene +, asumir que es número nacional
    # Determinar si ya incluye código de país
    # Ej: "34612345678" → "+34612345678"
    # Si empieza con el código del país sin el +, agregar +
    if cleaned.startswith(default_country_code.lstrip('+')):
        return '+' + cleaned
    else:
        # Asumir que es número nacional sin código de país
        # Agregar código de país por defecto
        national_number = cleaned
        # Si empieza con 0, quitarlo (ej: "06..." → "6...")
        if national_number.startswith('0'):
            national_number = national_number[1:]
        return default_country_code + national_number


def is_phone_match(phone1: str, phone2: str, default_country_code: str = "+34") -> bool:
    """
    Compara dos números normalizados para ver si son el mismo.

    Args:
        phone1, phone2: Números a comparar
        default_country_code: Código de país por defecto

    Returns:
        True si representan el mismo número
    """
    try:
        n1 = normalize_phone(phone1, default_country_code)
        n2 = normalize_phone(phone2, default_country_code)
        return n1 == n2
    except ValueError:
        return False


# Testing rápido
if __name__ == "__main__":
    test_cases = [
        ("+34612345678", "+34612345678"),
        ("34612345678", "+34612345678"),
        ("612345678", "+34612345678"),
        ("+34 612 345 678", "+34612345678"),
        ("+34-612-345-678", "+34612345678"),
        ("+(34)612345678", "+34612345678"),
        ("0034612345678", "+34612345678"),
        ("+34 0612 345 678", "+34612345678"),  # con 0 después del código
    ]

    print("Testing normalize_phone:")
    for input_phone, expected in test_cases:
        result = normalize_phone(input_phone)
        status = "✅" if result == expected else "❌"
        print(f"  {status} '{input_phone}' → '{result}' (expected: '{expected}')")
