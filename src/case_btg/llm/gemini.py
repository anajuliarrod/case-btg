import os
import sys
import time
from typing import Callable

from google import genai
from google.genai import errors, types

from case_btg.ingest import Document
from case_btg.schema import Extracao

DEFAULT_MODEL = "gemini-flash-latest"
RETRYABLE = {429, 500, 503, 504}
REQUEST_TIMEOUT_MS = 120_000
# Free tier: 20 requisições/dia por modelo. Uma rodada de tools (chamadas em paralelo)
# + a entrega final = 2 requisições por documento.
MAX_TOOL_ROUNDS = 1

FINAL_INSTRUCTION = (
    "Agora devolva a extração final no schema pedido. Mantenha os valores como estão no documento, "
    "mesmo que alguma verificação tenha falhado, e registre incoerências em observacoes."
)


class QuotaExhausted(Exception):
    """Cota diária esgotada: tentar de novo não adianta até a cota renovar."""


def model_name() -> str:
    return os.environ.get("GEMINI_MODEL", DEFAULT_MODEL)


def _client() -> genai.Client:
    key = os.environ.get("GEMINI_API_KEY")
    if not key:
        raise RuntimeError("GEMINI_API_KEY não definida: copie .env.example para .env e preencha a chave")
    return genai.Client(api_key=key, http_options=types.HttpOptions(timeout=REQUEST_TIMEOUT_MS))


def _generate(
    client: genai.Client, contents: list, config: types.GenerateContentConfig, retries: int = 5
) -> types.GenerateContentResponse:
    """generate_content com backoff exponencial para limite de taxa e indisponibilidade."""
    for attempt in range(retries):
        try:
            return client.models.generate_content(model=model_name(), contents=contents, config=config)
        except errors.APIError as e:
            if e.code == 429 and "PerDay" in str(e):
                raise QuotaExhausted(f"cota diária do modelo {model_name()} esgotada") from e
            if e.code not in RETRYABLE or attempt == retries - 1:
                raise
            wait = 5 * 2**attempt
            print(f"  [gemini] HTTP {e.code} — nova tentativa em {wait}s", file=sys.stderr, flush=True)
            time.sleep(wait)
    raise AssertionError("unreachable")


def _document_parts(doc: Document) -> list[types.Part]:
    header = f"Arquivo: {doc.path.name}\n"
    if doc.source == "texto_nativo":
        return [types.Part.from_text(text=f"{header}Texto extraído do PDF:\n<documento>\n{doc.text}\n</documento>")]
    parts = [types.Part.from_text(text=f"{header}O PDF não tem camada de texto; segue a imagem de cada página.")]
    for page in doc.pages:
        parts.append(types.Part.from_text(text=f"Página {page.number}:"))
        parts.append(types.Part.from_bytes(data=page.image_png, mime_type="image/png"))
    return parts


def run(doc: Document, system: str, fns: dict[str, Callable], call_tool: Callable[[str, dict], dict]
        ) -> tuple[Extracao, str]:
    client = _client()
    tool_config = types.GenerateContentConfig(
        system_instruction=system,
        tools=list(fns.values()),
        automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
        temperature=0,
    )
    contents: list = [types.UserContent(parts=_document_parts(doc))]

    for _ in range(MAX_TOOL_ROUNDS):
        response = _generate(client, contents, tool_config)
        contents.append(response.candidates[0].content)
        if not response.function_calls:
            break
        replies = [
            types.Part.from_function_response(name=call.name, response=call_tool(call.name, dict(call.args or {})))
            for call in response.function_calls
        ]
        contents.append(types.UserContent(parts=replies))

    contents.append(types.UserContent(parts=[types.Part.from_text(text=FINAL_INSTRUCTION)]))
    final = _generate(client, contents, types.GenerateContentConfig(
        system_instruction=system,
        response_mime_type="application/json",
        response_json_schema=Extracao.model_json_schema(),
        temperature=0,
    ))
    return Extracao.model_validate_json(final.text), final.model_version or model_name()
