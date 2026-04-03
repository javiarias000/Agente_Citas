# -*- coding: utf-8 -*-
"""
Divisor_Mensajes - Chain LLM para dividir mensajes largos

Equivalente a: @n8n/n8n-nodes-langchain.chainLlm

Cadena LangChain que divide mensajes en partes lógicas:
- Por cambios de tema
- Por tipo de contenido
- Por prioridad
- Usa LLM para análisis inteligente
"""

from typing import List, Dict, Any, Optional
from langchain_core.prompts import PromptTemplate
from langchain_core.output_parsers import JsonOutputParser
from langchain_openai import ChatOpenAI
from pydantic import BaseModel, Field
import structlog
from core.config import settings

logger = structlog.get_logger("chain.divisor")


class MessagePart(BaseModel):
    """Parte de un mensaje dividido"""
    parte: str = Field(description="Texto de la parte del mensaje")
    categoria: str = Field(
        description="Categoría: comando|pregunta|respuesta|informacion|urgente",
        pattern=r"^(comando|pregunta|respuesta|informacion|urgente)$"
    )
    prioridad: int = Field(
        description="Prioridad 1-5 (1=urgente, 5=opcional)",
        ge=1,
        le=5
    )
    razonamiento: str = Field(description="Por qué se asignó esta categoría y prioridad")


class DivisorChain:
    """
    Cadena para dividir mensajes en partes lógicas

    Implementa el nodo Divisor_Mensajes del workflow
    """

    DEFAULT_PROMPT_TEMPLATE = """
Eres un asistente especializado en análisis de mensajes.

Tu tarea: DIVIDIR el mensaje recibido en partes lógicas y homogéneas.

Reglas de división:
1. Cada parte debe tratar sobre un solo tema o intención
2. Si hay múltiples preguntas, separar cada pregunta
3. Si hay comandos, separar cada comando
4. Mantener el texto original de cada parte
5. Si una parte tiene sub-puntos, mantenerlos juntos

Categorías:
- comando: El usuario pide hacer algo
- pregunta: El usuario hace una pregunta
- respuesta: El usuario responde algo
- informacion: El usuario comparte información
- urgente: Contiene palabras urgentes o críticas

Prioridades:
1 = Urgente/crítico (ej: emergency, ahora, urgente, error)
2 = Alta prioridad
3 = Prioridad normal
4 = Baja prioridad
5 = Informativa/opcional

Mensaje a analizar:
{mensaje}

Analiza cuidadosamente y divide en partes. considera cambios de tema, intenciones múltiples, y estructura conversacional.

{format_instructions}
    """.strip()

    def __init__(
        self,
        llm: Optional[ChatOpenAI] = None,
        prompt_template: Optional[str] = None,
        model: str = None,
        temperature: float = 0.3,
        batch_size: int = 10
    ):
        """
        Inicializa cadena divisora

        Args:
            llm: Modelo LLM (se crea si no se provee)
            prompt_template: Template personalizado
            model: Modelo si se crea LLM (default: gpt-3.5-turbo)
            temperature: Creatividad (0-2, bajo para análisis)
            batch_size: Tamaño de batch para procesamiento masivo
        """
        self.llm = llm or ChatOpenAI(
            api_key=settings.OPENAI_API_KEY,
            model=model or settings.OPENAI_MODEL or "gpt-3.5-turbo",
            temperature=temperature,
            timeout=60,
            max_retries=3
        )
        self.prompt_template = prompt_template or self.DEFAULT_PROMPT_TEMPLATE
        self.batch_size = batch_size

        # Parser para JSON estructurado
        self.parser = JsonOutputParser(pydantic_object=MessagePart)

        # Crear prompt con formato
        self.prompt = PromptTemplate(
            template=self.prompt_template,
            input_variables=["mensaje"],
            partial_variables={
                "format_instructions": self.parser.get_format_instructions()
            }
        )

        # Chain: prompt | llm | parser
        self.chain = self.prompt | self.llm | self.parser

        self.logger = logger.bind(chain="divisor")

    async def process_single(
        self,
        mensaje: str,
        extra_context: Optional[str] = None
    ) -> List[MessagePart]:
        """
        Divide un mensaje en partes lógicas

        Args:
            mensaje: Mensaje a analizar
            extra_context: Contexto adicional (historial, etc.)

        Returns:
            Lista de MessagePart con divisiones
        """
        if not mensaje or not mensaje.strip():
            self.logger.warning("Mensaje vacío recibido")
            return []

        self.logger.info(
            "Procesando mensaje con divisor",
            message_length=len(mensaje)
        )

        try:
            # Enriquecer prompt con contexto si existe
            final_prompt = self.prompt_template
            if extra_context:
                final_prompt = f"Contexto adicional:\n{extra_context}\n\n{self.prompt_template}"

            # Crear chain dinámico si hay contexto extra
            chain = PromptTemplate(
                template=final_prompt,
                input_variables=["mensaje"],
                partial_variables={
                    "format_instructions": self.parser.get_format_instructions()
                }
            ) | self.llm | self.parser

            # Invocar chain
            result = await chain.ainvoke({
                "mensaje": mensaje
            })

            # Normalizar resultado a lista
            if isinstance(result, dict):
                result = [result]
            elif not isinstance(result, list):
                self.logger.warning("Resultado inesperado", result_type=type(result))
                result = []

            # Validar y mapear a MessagePart
            parts = []
            for item in result:
                try:
                    part = MessagePart(**item)
                    parts.append(part)
                except Exception as e:
                    self.logger.warning("Parte inválida ignorada", error=str(e))

            self.logger.info(
                "Mensaje dividido exitosamente",
                parts_count=len(parts),
                avg_priority=sum(p.prioridad for p in parts) / len(parts) if parts else 0
            )

            return parts

        except Exception as e:
            self.logger.error("Error dividiendo mensaje", error=str(e))
            # Fallback: dividir por líneas
            return self._fallback_split(mensaje)

    async def process_batch(
        self,
        mensajes: List[str],
        return_indices: bool = False
    ) -> Dict[str, Any]:
        """
        Procesa lote de mensajes

        Args:
            mensajes: Lista de mensajes
            return_indices: Si incluir índices originales

        Returns:
            Dict con resultados por índice
        """
        self.logger.info("Procesando batch de mensajes", count=len(mensajes))

        results = {}
        for idx, mensaje in enumerate(mensajes):
            try:
                parts = await self.process_single(mensaje)
                if return_indices:
                    results[idx] = {
                        "parts": [p.dict() for p in parts],
                        "count": len(parts),
                        "original_message": mensaje[:100]
                    }
                else:
                    results[mensaje[:50]] = [p.dict() for p in parts]
            except Exception as e:
                self.logger.error(f"Error procesando mensaje {idx}", error=str(e))
                results[idx if return_indices else mensaje[:50]] = {
                    "error": str(e),
                    "parts": []
                }

        return {
            "total_mensajes": len(mensajes),
            "resultados": results,
            "timestamp": datetime.utcnow().isoformat()
        }

    def _fallback_split(self, mensaje: str) -> List[MessagePart]:
        """
        Fallback simple: divide por párrafos/líneas

        Usado si LLM falla
        """
        lines = mensaje.split('\n')
        paragraphs = [p.strip() for p in mensaje.split('\n\n') if p.strip()]

        if len(paragraphs) > 1:
            parts = []
            for i, para in enumerate(paragraphs[:3]):  # Max 3 partes en fallback
                part = MessagePart(
                    parte=para[:500],  # Truncar
                    categoria="informacion",
                    prioridad=3,  # Normal
                    razonamiento="División por párrafo (fallback)"
                )
                parts.append(part)
            return parts
        else:
            # Un solo mensaje
            return [MessagePart(
                parte=mensaje[:2000],
                categoria="informacion",
                prioridad=3,
                razonamiento="Mensaje único (fallback)"
            )]

    def validate_result(self, parts: List[MessagePart]) -> Dict[str, Any]:
        """
        Valida resultado de división

        Returns:
            Dict con métricas de calidad
        """
        if not parts:
            return {
                "valid": False,
                "error": "No hay partes",
                "quality_score": 0.0
            }

        total_chars = sum(len(p.parte) for p in parts)
        original_coverage = total_chars / len(parts[0].parte) if parts else 0

        issues = []
        for i, part in enumerate(parts):
            if len(part.parte.strip()) < 10:
                issues.append(f"Parte {i} muy corta ({len(part.parte)} chars)")
            if part.prioridad not in [1, 2, 3, 4, 5]:
                issues.append(f"Parte {i} prioridad inválida: {part.prioridad}")
            if part.categoria not in ["comando", "pregunta", "respuesta", "informacion", "urgente"]:
                issues.append(f"Parte {i} categoría inválida: {part.categoria}")

        quality_score = 1.0 - (len(issues) * 0.2)
        quality_score = max(0.0, min(1.0, quality_score))

        return {
            "valid": len(issues) == 0,
            "total_parts": len(parts),
            "total_chars": total_chars,
            "original_coverage": original_coverage,
            "avg_priority": sum(p.prioridad for p in parts) / len(parts),
            "category_distribution": {
                cat: sum(1 for p in parts if p.categoria == cat)
                for cat in ["comando", "pregunta", "respuesta", "informacion", "urgente"]
            },
            "issues": issues,
            "quality_score": quality_score
        }


# ========== FUNCIONES DE CONVENIENCIA ==========

async def dividir_mensaje(
    mensaje: str,
    llm_model: str = None,
    return_structured: bool = True
) -> Dict[str, Any]:
    """
    Función simple para dividir un mensaje

    Args:
        mensaje: Mensaje a dividir
        llm_model: Modelo LLM
        return_structured: Si retornar objeto estructurado o JSON plano

    Returns:
        Dict con partes y metadata
    """
    chain = DivisorChain(model=llm_model)
    parts = await chain.process_single(mensaje)

    validation = chain.validate_result(parts)

    result = {
        "mensaje_original": mensaje,
        "total_partes": len(parts),
        "partes": [p.dict() for p in parts] if return_structured else [p.parte for p in parts],
        "validacion": validation
    }

    return result
