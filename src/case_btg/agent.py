"""Agente de extração: o LLM extrai e se autoverifica com as tools; tudo fica em cache para replay."""

import importlib
import json
import os
import sys
from datetime import datetime
from typing import Callable, Literal

from case_btg.config import CACHE_DIR, PROMPTS_DIR, ROOT
from case_btg.ingest import Document
from case_btg.schema import Auditoria, Extracao
from case_btg.tools.amounts import check_gross_net
from case_btg.tools.dates import validate_dates
from case_btg.tools.evidence import locate_quote
from case_btg.tools.golden import lookup_golden_record

PROMPT_VERSION = "v1"
Mode = Literal["auto", "replay", "refresh"]


class CacheMiss(Exception):
    pass


def provider() -> str:
    return os.environ.get("LLM_PROVIDER", "gemini")


# Cada backend expõe run(doc, system, fns, call_tool) -> (Extracao, nome_do_modelo).
BACKENDS = {
    "gemini": "case_btg.llm.gemini",
    "claude": "case_btg.llm.claude",
}


def _backend():
    if provider() not in BACKENDS:
        raise ValueError(f"LLM_PROVIDER desconhecido: {provider()!r}. Opções: {', '.join(BACKENDS)}")
    return importlib.import_module(BACKENDS[provider()])


def _tools(doc: Document) -> dict[str, Callable]:
    def verificar_trecho(trecho: str) -> dict:
        """Confere se um trecho existe literalmente no texto do documento. Use antes de citar."""
        return locate_quote(doc.text, trecho)

    fns = [lookup_golden_record, validate_dates, check_gross_net]
    if doc.source == "texto_nativo":
        fns.append(verificar_trecho)
    return {f.__name__: f for f in fns}


def _call_llm(doc: Document) -> dict:
    system = (PROMPTS_DIR / f"extraction_{PROMPT_VERSION}.md").read_text(encoding="utf-8")
    fns = _tools(doc)
    trace: list[dict] = []

    def call_tool(name: str, args: dict) -> dict:
        if name not in fns:
            result = {"erro": f"ferramenta desconhecida: {name}"}
        else:
            try:
                out = fns[name](**args)
                result = out if isinstance(out, dict) else {"resultado": out}
            except (TypeError, ValueError) as e:
                result = {"erro": f"{type(e).__name__}: {e}"}
        trace.append({"ferramenta": name, "argumentos": args, "resultado": result})
        print(f"  [tool] {name}", file=sys.stderr, flush=True)
        return result

    extracao, model = _backend().run(doc, system, fns, call_tool)
    return {
        "arquivo": doc.path.name,
        "sha256": doc.sha256,
        "prompt_versao": PROMPT_VERSION,
        "provider": provider(),
        "modelo": model,
        "processado_em": datetime.now().isoformat(timespec="seconds"),
        "chamadas_ferramentas": trace,
        "extracao": extracao.model_dump(mode="json"),
    }


def extract(doc: Document, mode: Mode = "auto") -> tuple[Extracao, Auditoria]:
    """auto: usa o cache se existir; replay: só cache (sem LLM); refresh: sempre chama o LLM."""
    path = CACHE_DIR / f"{doc.path.stem}.{doc.sha256[:12]}.{PROMPT_VERSION}.{provider()}.json"
    if path.exists() and mode != "refresh":
        payload = json.loads(path.read_text(encoding="utf-8"))
    elif mode == "replay":
        raise CacheMiss(f"sem resposta em cache para {doc.path.name} ({provider()}, prompt {PROMPT_VERSION})")
    else:
        payload = _call_llm(doc)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    auditoria = Auditoria(
        modelo=f"{payload['provider']}/{payload['modelo']}",
        prompt_versao=payload["prompt_versao"],
        processado_em=payload["processado_em"],
        resposta_llm_cache=str(path.relative_to(ROOT)),
    )
    return Extracao.model_validate(payload["extracao"]), auditoria
