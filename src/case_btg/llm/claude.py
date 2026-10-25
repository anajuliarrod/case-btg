"""Backend Claude via Claude Agent SDK, autenticado pelo login local do Claude Code.
Pensado para testes locais; a execução de referência usa Gemini com API key."""

import asyncio
import base64
import inspect
import json
import os
from typing import Callable

from claude_agent_sdk import AssistantMessage, ClaudeAgentOptions, ResultMessage, create_sdk_mcp_server, query, tool

from case_btg.ingest import Document
from case_btg.schema import Extracao

DEFAULT_MODEL = "sonnet"
SERVER = "case"
MAX_TURNS = 10


def model_name() -> str:
    return os.environ.get("CLAUDE_MODEL", DEFAULT_MODEL)


def _input_schema(fn: Callable) -> dict:
    params = inspect.signature(fn).parameters
    return {
        "type": "object",
        "properties": {name: {"type": "string"} for name in params},
        "required": [n for n, p in params.items() if p.default is inspect.Parameter.empty],
    }


def _as_tool(fn: Callable, call_tool: Callable[[str, dict], dict]):
    @tool(fn.__name__, inspect.getdoc(fn) or fn.__name__, _input_schema(fn))
    async def handler(args: dict) -> dict:
        result = call_tool(fn.__name__, {k: v for k, v in args.items() if v is not None})
        return {"content": [{"type": "text", "text": json.dumps(result, ensure_ascii=False)}]}

    return handler


def _content(doc: Document) -> list[dict]:
    header = f"Arquivo: {doc.path.name}\n"
    if doc.source == "texto_nativo":
        return [{"type": "text", "text": f"{header}Texto extraído do PDF:\n<documento>\n{doc.text}\n</documento>"}]
    blocks = [{"type": "text", "text": f"{header}O PDF não tem camada de texto; segue a imagem de cada página."}]
    for page in doc.pages:
        blocks.append({"type": "text", "text": f"Página {page.number}:"})
        blocks.append({"type": "image", "source": {"type": "base64", "media_type": "image/png",
                                                   "data": base64.b64encode(page.image_png).decode()}})
    return blocks


async def _run(doc: Document, system: str, fns: dict[str, Callable], call_tool) -> tuple[Extracao, str]:
    options = ClaudeAgentOptions(
        system_prompt=system,
        model=model_name(),
        tools=[],  # sem as ferramentas embutidas do Claude Code: só as nossas
        mcp_servers={SERVER: create_sdk_mcp_server(name=SERVER, version="1.0.0",
                                                   tools=[_as_tool(f, call_tool) for f in fns.values()])},
        allowed_tools=[f"mcp__{SERVER}__{name}" for name in fns],
        setting_sources=[],
        output_format={"type": "json_schema", "schema": Extracao.model_json_schema()},
        max_turns=MAX_TURNS,
    )

    async def prompt():
        yield {"type": "user", "message": {"role": "user", "content": _content(doc)},
               "parent_tool_use_id": None, "session_id": "default"}

    model, result = model_name(), None
    async for message in query(prompt=prompt(), options=options):
        if isinstance(message, AssistantMessage):
            model = message.model
        elif isinstance(message, ResultMessage):
            result = message
    if result is None or result.is_error or result.structured_output is None:
        raise RuntimeError(f"Claude não entregou saída estruturada ({getattr(result, 'subtype', 'sem resultado')})")
    return Extracao.model_validate(result.structured_output), model


def run(doc: Document, system: str, fns: dict[str, Callable], call_tool: Callable[[str, dict], dict]
        ) -> tuple[Extracao, str]:
    return asyncio.run(_run(doc, system, fns, call_tool))
