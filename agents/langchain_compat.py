"""
Compatibilidad para LangChain versiones 0.1.x y 0.2.x
Centraliza imports que cambiaron entre versiones.
"""

from typing import Any, Dict, List, Sequence
from langchain_core.language_models import BaseLanguageModel
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.runnables import Runnable, RunnablePassthrough
from langchain_core.tools import BaseTool, StructuredTool
from langchain.tools import tool

# Importar format_tool_to_openai_tool
try:
    from langchain_community.tools.convert_to_openai import format_tool_to_openai_tool
except ImportError:
    # En versiones más nuevas o alternativas, implementamos manualmente
    def format_tool_to_openai_tool(tool):
        """Convierte un BaseTool al formato OpenAI function."""
        # Obtener nombre y descripción
        name = getattr(tool, "name", getattr(tool, "__name__", str(tool)))
        description = getattr(tool, "description", "")
        # Obtener parámetros (schema)
        if hasattr(tool, "args_schema"):
            try:
                params = tool.args_schema.schema()
            except Exception:
                params = {"type": "object", "properties": {}}
        elif hasattr(tool, "run") and hasattr(tool.run, "__annotations__"):
            # Construcción simple desde annotations (muy básico)
            params = {"type": "object", "properties": {}, "required": []}
            for param, typ in tool.run.__annotations__.items():
                if param != "return":
                    params["properties"][param] = {"type": "string"}
                    # No manejamos opcionales/requeridos aquí
        else:
            params = {"type": "object", "properties": {}}
        return {
            "type": "function",
            "function": {
                "name": name,
                "description": description,
                "parameters": params
            }
        }

# Importar format_to_openai_tool_messages y OpenAIToolsAgentOutputParser
try:
    from langchain.agents.format_scratchpad.openai_tools import format_to_openai_tool_messages
except ImportError:
    # Si no existe, implementación simple
    def format_to_openai_tool_messages(intermediate_steps: List) -> List:
        """Formatea pasos intermedios para OpenAI tools"""
        messages = []
        for action, observation in intermediate_steps:
            # Extraer tool name y input, soportando dict u objeto
            if isinstance(action, dict):
                tool_name = action.get("tool", "")
                tool_input = action.get("tool_input", {})
            else:
                tool_name = getattr(action, "tool", "")
                tool_input = getattr(action, "tool_input", {})
            # Generar ID único para el tool call
            call_id = "call_" + str(hash(str(tool_name) + str(tool_input)))[:8]
            messages.append({"role": "assistant", "content": None, "tool_calls": [{
                "id": call_id,
                "type": "function",
                "function": {
                    "name": tool_name,
                    "arguments": str(tool_input)
                }
            }]})
            messages.append({"role": "tool", "content": str(observation), "tool_call_id": call_id})
        return messages

try:
    from langchain.agents.output_parsers.openai_tools import OpenAIToolsAgentOutputParser
except ImportError:
    # Parser simple que extrae la respuesta final
    class OpenAIToolsAgentOutputParser:
        def __call__(self, response):
            # En versión simple, solo devolvemos el texto
            if hasattr(response, 'content'):
                return response.content
            return str(response)


def create_openai_tools_agent(
    llm: BaseLanguageModel,
    tools: Sequence[BaseTool],
    prompt: ChatPromptTemplate
) -> Runnable:
    """Crea un agente con herramientas OpenAI (compatible con múltiples versiones)"""
    missing_vars = {"agent_scratchpad"}.difference(prompt.input_variables)
    if missing_vars:
        raise ValueError(f"Prompt missing required variables: {missing_vars}")

    llm_with_tools = llm.bind(
        tools=[format_tool_to_openai_tool(tool) for tool in tools]
    )

    agent = (
        RunnablePassthrough.assign(
            agent_scratchpad=lambda x: format_to_openai_tool_messages(
                x["intermediate_steps"]
            )
        )
        | prompt
        | llm_with_tools
        | OpenAIToolsAgentOutputParser()
    )
    return agent


# Wrapper para compatibilidad con AgentExecutor antiguo
class AgentExecutor(Runnable):
    """Wrapper compatible para execute en estilo antiguo"""

    def __init__(self, agent: Runnable, tools: List[BaseTool], **kwargs):
        self.agent = agent
        self.tools = tools
        # Ignoramos kwargs no usados: memory, verbose, handle_parsing_errors, etc.

    async def ainvoke(self, input_dict: Dict[str, Any]) -> Dict[str, Any]:
        """Ejecuta el agente y devuelve resultado en formato antiguo"""
        result = await self.agent.ainvoke(input_dict)
        # En el API nueva, el resultado es directamente el texto o un dict
        # Necesitamos convertir al formato antiguo: {'output': str, 'intermediate_steps': [...]}
        if isinstance(result, str):
            return {"output": result, "intermediate_steps": []}
        elif isinstance(result, dict):
            return result
        else:
            return {"output": str(result), "intermediate_steps": []}

    def invoke(self, input_dict: Dict[str, Any]) -> Dict[str, Any]:
        """Versión sincrona"""
        import asyncio
        return asyncio.run(self.ainvoke(input_dict))
