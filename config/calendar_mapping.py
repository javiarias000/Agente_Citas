#!/usr/bin/env python3
"""
Configuración de mapeo de servicios a calendarios de Google.

Este módulo define:
- KEYWORD_MAPPING: palabras clave coloquiales → servicio oficial del CSV
- SERVICE_MAPPING: servicio oficial → odontólogo (Calendar ID)
- SERVICE_DURATION: servicio oficial → duración en minutos
"""

from typing import Dict, Optional, Tuple

# ============================================
# PALABRAS CLAVE → SERVICIO OFICIAL
# cliente dice: "quiero limpieza" → se mapea a "Limpieza dental profesional (profilaxis)"
# ============================================
KEYWORD_MAPPING: Dict[str, str] = {
    # === LIMPIEZA ===
    "limpieza": "Limpieza dental profesional (profilaxis)",
    "limpiar": "Limpieza dental profesional (profilaxis)",
    "profilaxis": "Limpieza dental profesional (profilaxis)",
    "sarro": "Limpieza dental profesional (profilaxis)",
    "limpieza dental": "Limpieza dental profesional (profilaxis)",
    "limpieza profunda": "Limpieza dental profesional (profilaxis)",

    # === CONSULTA ===
    "consulta": "Consulta inicial",
    "revisión": "Consulta de control",
    "revision": "Consulta de control",
    "evaluación": "Consulta inicial",
    "evaluacion": "Consulta inicial",
    "chequeo": "Consulta de control",
    "control": "Consulta de control",

    # === EMPASTE / CARIES ===
    "empaste": "Empaste de resina (obturación)",
    "caries": "Empaste de resina (obturación)",
    "obturación": "Empaste de resina (obturación)",
    "obturacion": "Empaste de resina (obturación)",
    "restauración": "Empaste de resina (obturación)",
    "restauracion": "Empaste de resina (obturación)",
    "diente dañado": "Empaste de resina (obturación)",

    # === EXTRACCIÓN ===
    "extracción": "Extracción simple",
    "extraccion": "Extracción simple",
    "sacar diente": "Extracción simple",
    "sacar muela": "Extracción simple",
    "muela mal": "Extracción simple",

    # === ENDODONCIA (CONDUCTO) ===
    "conducto": "Endodoncia unirradicular (1 conducto)",
    "nerve": "Endodoncia unirradicular (1 conducto)",  # inglés
    "nervio": "Endodoncia unirradicular (1 conducto)",
    "tratamiento de conducto": "Endodoncia unirradicular (1 conducto)",
    "endodoncia": "Endodoncia unirradicular (1 conducto)",

    # === BLANQUEAMIENTO ===
    "blanquear": "Blanqueamiento dental en consultorio",
    "blanqueamiento": "Blanqueamiento dental en consultorio",
    "dientes blancos": "Blanqueamiento dental en consultorio",
    "blanqueamiento dental": "Blanqueamiento dental en consultorio",

    # === ORTODONCIA ===
    "frenos": "Ortodoncia metálica",
    "braquets": "Ortodoncia metálica",
    "braces": "Ortodoncia metálica",
    "alinear": "Ortodoncia metálica",
    "alineadores": "Alineadores transparentes (Invisalign)",
    "invisalign": "Alineadores transparentes (Invisalign)",
    "ortodoncia": "Ortodoncia metálica",

    # === IMPLANTES ===
    "implante": "Implante dental unitario completo",
    "tornillo": "Implante dental unitario completo",
    "implante dental": "Implante dental unitario completo",
    "diente postizo": "Implante dental unitario completo",

    # === CARILLAS / CORONAS ===
    "carilla": "Carilla de porcelana",
    "laminas": "Carilla de porcelana",
    "laminilla": "Carilla de porcelana",
    "corona": "Corona de porcelana",
    "funda": "Corona de porcelana",
    "diente roto": "Empaste de resina (obturación)",  # o carilla según daño

    # === FLUOR / PROTECCIÓN ===
    "fluor": "Fluorización",
    "flúor": "Fluorización",
    "flouride": "Fluorización",
    "protección": "Fluorización",

    # === ODONTOPEDIATRÍA ===
    "niño": "Consulta pediátrica",
    "niños": "Consulta pediátrica",
    "pediatra": "Consulta pediátrica",
    "odontopediatría": "Consulta pediátrica",
    "diente de leche": "Consulta pediátrica",
    "sellantes": "Sellantes de fosas y fisuras",

    # === PERIODONCIA ===
    "encías": "Tratamiento de gingivitis",
    "encia": "Tratamiento de gingivitis",
    "periodontal": "Tratamiento de gingivitis",
    "gingivitis": "Tratamiento de gingivitis",
    "enfermedad de encías": "Tratamiento de periodontitis",

    # === RETENEDORES ===
    "retenedor": "Retenedores post-ortodoncia",
    "retainer": "Retenedores post-ortodoncia",
    "mantenedor": "Retenedores post-ortodoncia",
}

# ============================================
# SERVICIO OFICIAL → ODONTÓLOGO (Calendar ID)
# ============================================
SERVICE_TO_DENTIST: Dict[str, str] = {
    # === ORTODONCIA (todos a Jorge) ===
    "Ortodoncia metálica": "jorge.arias.amauta@gmail.com",
    "Ortodoncia cerámica (estética)": "jorge.arias.amauta@gmail.com",
    "Ortodoncia lingual": "jorge.arias.amauta@gmail.com",
    "Alineadores transparentes (Invisalign)": "jorge.arias.amauta@gmail.com",
    "Retenedores post-ortodoncia": "jorge.arias.amauta@gmail.com",
    "Ortodoncia interceptiva niños": "jorge.arias.amauta@gmail.com",

    # === CIRUGÍA (todos a Javi) ===
    "Extracción simple": "javiarias000@gmail.com",
    "Extracción de cordales (muelas del juicio)": "javiarias000@gmail.com",
    "Apicectomía": "javiarias000@gmail.com",
    "Cirugía periodontal": "javiarias000@gmail.com",
    "Quistectomía": "javiarias000@gmail.com",
    "Frenectomía": "javiarias000@gmail.com",
    "Elevación de seno maxilar": "javiarias000@gmail.com",
    "Regeneración ósea guiada": "javiarias000@gmail.com",

    # === ODONTOLOGÍA GENERAL → Jorge (odontólogo general) ===
    "Consulta inicial": "jorge.arias.amauta@gmail.com",
    "Consulta de control": "jorge.arias.amauta@gmail.com",
    "Limpieza dental profesional (profilaxis)": "jorge.arias.amauta@gmail.com",
    "Empaste de resina (obturación)": "jorge.arias.amauta@gmail.com",
    "Fluorización": "jorge.arias.amauta@gmail.com",

    # === ENDODONCIA → Jorge (general) ===
    "Endodoncia unirradicular (1 conducto)": "jorge.arias.amauta@gmail.com",
    "Endodoncia birradicular (2 conductos)": "jorge.arias.amauta@gmail.com",
    "Endodoncia multirradicular (3+ conductos)": "jorge.arias.amauta@gmail.com",
    "Retratamiento endodóntico": "jorge.arias.amauta@gmail.com",

    # === PERIODONCIA → Javi (cirugía) ===
    "Tratamiento de gingivitis": "javiarias000@gmail.com",
    "Tratamiento de periodontitis": "javiarias000@gmail.com",
    "Mantenimiento periodontal": "javiarias000@gmail.com",

    # === IMPLANTOLOGÍA → Javi (cirugía) ===
    "Implante dental unitario completo": "javiarias000@gmail.com",
    "Implantes múltiples": "javiarias000@gmail.com",
    "All-on-4 por arcada": "javiarias000@gmail.com",

    # === ESTÉTICA → Jorge (general) ===
    "Blanqueamiento dental en consultorio": "jorge.arias.amauta@gmail.com",
    "Blanqueamiento domiciliario": "jorge.arias.amauta@gmail.com",
    "Carilla de porcelana": "jorge.arias.amauta@gmail.com",
    "Carilla de resina (composite)": "jorge.arias.amauta@gmail.com",
    "Diseño de sonrisa digital": "jorge.arias.amauta@gmail.com",
    "Corona de porcelana": "jorge.arias.amauta@gmail.com",
    "Incrustaciones inlays onlays": "jorge.arias.amauta@gmail.com",

    # === ODONTOPEDIATRÍA → Jorge (general) ===
    "Consulta pediátrica": "jorge.arias.amauta@gmail.com",
    "Sellantes de fosas y fisuras": "jorge.arias.amauta@gmail.com",
    "Corona de acero": "jorge.arias.amauta@gmail.com",
    "Pulpotomía (nervio temporal)": "jorge.arias.amauta@gmail.com",
    "Mantenedor de espacio": "jorge.arias.amauta@gmail.com",
}

# ============================================
# SHORT KEY → DOCTOR EMAIL (mapeo directo)
# ============================================
SHORT_KEY_TO_EMAIL: Dict[str, str] = {
    "consulta":        "jorge.arias.amauta@gmail.com",
    "limpieza":        "jorge.arias.amauta@gmail.com",
    "empaste":         "jorge.arias.amauta@gmail.com",
    "extraccion":      "javiarias000@gmail.com",
    "endodoncia":      "jorge.arias.amauta@gmail.com",
    "ortodoncia":      "jorge.arias.amauta@gmail.com",
    "cirugia":         "javiarias000@gmail.com",
    "implantes":       "javiarias000@gmail.com",
    "estetica":        "jorge.arias.amauta@gmail.com",
    "odontopediatria": "jorge.arias.amauta@gmail.com",
    "blanqueamiento":  "jorge.arias.amauta@gmail.com",
    "revision":        "jorge.arias.amauta@gmail.com",
}


def get_email_for_short_key(short_key: str) -> Optional[str]:
    """Obtiene email del doctor para una clave corta de servicio."""
    return SHORT_KEY_TO_EMAIL.get(short_key.lower().strip())

# ============================================
# SERVICIO OFICIAL → DURACIÓN (minutos)
# ============================================
SERVICE_DURATION: Dict[str, int] = {
    # Odontología General
    "Consulta inicial": 60,
    "Consulta de control": 60,
    "Limpieza dental profesional (profilaxis)": 60,
    "Empaste de resina (obturación)": 60,
    "Extracción simple": 60,
    "Fluorización": 60,

    # Endodoncia
    "Endodoncia unirradicular (1 conducto)": 60,
    "Endodoncia birradicular (2 conductos)": 90,
    "Endodoncia multirradicular (3+ conductos)": 90,
    "Retratamiento endodóntico": 90,

    # Periodoncia
    "Tratamiento de gingivitis": 60,
    "Tratamiento de periodontitis": 60,  # por cuadrante puede ser más
    "Mantenimiento periodontal": 60,

    # Ortodoncia
    "Ortodoncia metálica": 60,
    "Ortodoncia cerámica (estética)": 60,
    "Ortodoncia lingual": 60,
    "Alineadores transparentes (Invisalign)": 60,
    "Retenedores post-ortodoncia": 30,
    "Ortodoncia interceptiva niños": 60,

    # Implantología
    "Implante dental unitario completo": 90,
    "Implantes múltiples": 90,
    "All-on-4 por arcada": 90,  # por arcada
    "Elevación de seno maxilar": 90,
    "Regeneración ósea guiada": 90,

    # Estética
    "Blanqueamiento dental en consultorio": 90,
    "Blanqueamiento domiciliario": 30,  # consulta de entrega
    "Carilla de porcelana": 60,
    "Carilla de resina (composite)": 60,
    "Diseño de sonrisa digital": 30,
    "Corona de porcelana": 60,
    "Incrustaciones inlays onlays": 60,

    # Odontopediatría
    "Consulta pediátrica": 60,
    "Sellantes de fosas y fisuras": 60,
    "Corona de acero": 60,
    "Pulpotomía (nervio temporal)": 60,
    "Mantenedor de espacio": 60,

    # Cirugía
    "Extracción de cordales (muelas del juicio)": 60,
    "Frenectomía": 60,
    "Quistectomía": 90,
    "Apicectomía": 90,
}

# ============================================
# CATEGORÍAS PADRES (para agrupación)
# ============================================
CATEGORY_MAPPING = {
    "Consulta inicial": "Odontología General",
    "Consulta de control": "Odontología General",
    "Limpieza dental profesional (profilaxis)": "Odontología General",
    "Empaste de resina (obturación)": "Odontología General",
    "Extracción simple": "Odontología General",
    "Fluorización": "Odontología General",

    "Endodoncia unirradicular (1 conducto)": "Endodoncia",
    "Endodoncia birradicular (2 conductos)": "Endodoncia",
    "Endodoncia multirradicular (3+ conductos)": "Endodoncia",
    "Retratamiento endodóntico": "Endodoncia",

    "Tratamiento de gingivitis": "Periodoncia",
    "Tratamiento de periodontitis": "Periodoncia",
    "Mantenimiento periodontal": "Periodoncia",

    "Ortodoncia metálica": "Ortodoncia",
    "Ortodoncia cerámica (estética)": "Ortodoncia",
    "Ortodoncia lingual": "Ortodoncia",
    "Alineadores transparentes (Invisalign)": "Ortodoncia",
    "Retenedores post-ortodoncia": "Ortodoncia",
    "Ortodoncia interceptiva niños": "Ortodoncia",

    "Implante dental unitario completo": "Implantología",
    "Implantes múltiples": "Implantología",
    "All-on-4 por arcada": "Implantología",
    "Elevación de seno maxilar": "Implantología",
    "Regeneración ósea guiada": "Implantología",

    "Blanqueamiento dental en consultorio": "Estética Dental",
    "Blanqueamiento domiciliario": "Estética Dental",
    "Carilla de porcelana": "Estética Dental",
    "Carilla de resina (composite)": "Estética Dental",
    "Diseño de sonrisa digital": "Estética Dental",
    "Corona de porcelana": "Estética Dental",
    "Incrustaciones inlays onlays": "Estética Dental",

    "Consulta pediátrica": "Odontopediatría",
    "Sellantes de fosas y fisuras": "Odontopediatría",
    "Corona de acero": "Odontopediatría",
    "Pulpotomía (nervio temporal)": "Odontopediatría",
    "Mantenedor de espacio": "Odontopediatría",

    # Cirugía
    "Extracción de cordales (muelas del juicio)": "Cirugía Oral",
    "Apicectomía": "Cirugía Oral",
    "Cirugía periodontal": "Cirugía Oral",
    "Quistectomía": "Cirugía Oral",
    "Frenectomía": "Cirugía Oral",
    "Elevación de seno maxilar": "Cirugía Oral",
    "Regeneración ósea guiada": "Cirugía Oral",
}

# ============================================
# FUNCIONES HELPER
# ============================================
def get_service_from_keyword(keyword: str) -> str:
    """
    Convierte una palabra clave coloquial en servicio oficial.

    Args:
        keyword: Palabra que dice el cliente (ej: "limpieza")

    Returns:
        Nombre oficial del servicio (del CSV)

    Raises:
        ValueError: Si no se puede mapear
    """
    keyword_lower = keyword.lower().strip()

    # Búsqueda exacta
    if keyword_lower in KEYWORD_MAPPING:
        return KEYWORD_MAPPING[keyword_lower]

    # Búsqueda parcial (si la keyword está contenida en algún mapeo)
    for key, service in KEYWORD_MAPPING.items():
        if keyword_lower in key or key in keyword_lower:
            return service

    raise ValueError(
        f"No puedo identificar el servicio: '{keyword}'. "
        f"Servicios disponibles: {', '.join(sorted(set(KEYWORD_MAPPING.values())))}"
    )


def get_dentist_for_service(service_name: str) -> str:
    """
    Obtiene el Calendar ID del odontólogo que atiende un servicio.

    Args:
        service_name: Nombre oficial del servicio

    Returns:
        Calendar ID (email) del odontólogo

    Raises:
        ValueError: Si el servicio no tiene odontólogo asignado
    """
    if service_name not in SERVICE_TO_DENTIST:
        raise ValueError(f"Servicio '{service_name}' no tiene odontólogo asignado")

    return SERVICE_TO_DENTIST[service_name]


def get_duration_for_service(service_name: str) -> int:
    """
    Obtiene la duración en minutos para un servicio.

    Args:
        service_name: Nombre oficial del servicio

    Returns:
        Duración en minutos

    Raises:
        ValueError: Si el servicio no tiene duración definida
    """
    if service_name not in SERVICE_DURATION:
        raise ValueError(f"Servicio '{service_name}' no tiene duración definida")

    return SERVICE_DURATION[service_name]


def get_category_for_service(service_name: str) -> str:
    """
    Obtiene la categoría padre de un servicio.

    Args:
        service_name: Nombre oficial del servicio

    Returns:
        Nombre de la categoría (ej: "Ortodoncia", "Odontología General")
    """
    return CATEGORY_MAPPING.get(service_name, "General")


def list_available_services() -> list[str]:
    """Retorna lista de servicios oficiales disponibles"""
    return sorted(set(KEYWORD_MAPPING.values()))


def list_all_services_with_details() -> list[dict]:
    """Retorna todos los servicios con su odontólogo y duración"""
    result = []
    for service in sorted(set(KEYWORD_MAPPING.values())):
        try:
            result.append({
                "servicio": service,
                "categoría": get_category_for_service(service),
                "odontólogo": get_dentist_for_service(service),
                "duración_min": get_duration_for_service(service)
            })
        except ValueError:
            continue
    return result
