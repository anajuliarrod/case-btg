"""Ancoragem das extrações no texto do documento (anti-alucinação) e contagem de menções."""

import re
import unicodedata
from datetime import date
from decimal import Decimal

MONTHS = ["janeiro", "fevereiro", "marco", "abril", "maio", "junho",
          "julho", "agosto", "setembro", "outubro", "novembro", "dezembro"]


def normalize(text: str) -> str:
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode().lower()
    text = re.sub(r"[“”\"'`]", "", text)
    return re.sub(r"\s+", " ", text).strip()


def locate_quote(text: str, quote: str) -> dict:
    """O trecho citado existe literalmente (módulo acentos/espaços/aspas) no texto?"""
    norm_text, norm_quote = normalize(text), normalize(quote)
    found = bool(norm_quote) and norm_quote in norm_text
    line = None
    if found:
        lines = text.splitlines()
        # trecho pode quebrar linha: a menor janela (1..5 linhas) que o contém dá a linha inicial
        line = next(
            (
                i + 1
                for size in range(1, 6)
                for i in range(len(lines))
                if norm_quote in normalize(" ".join(lines[i : i + size]))
            ),
            None,
        )
    return {"encontrado": found, "linha": line}


def _date_patterns(d: date) -> list[str]:
    return [
        rf"\b0?{d.day}/{d.month:02d}/{d.year}\b",
        rf"\b0?{d.day} de {MONTHS[d.month - 1]} de {d.year}\b",
    ]


def _money_patterns(value: Decimal) -> list[str]:
    # 0.4275000000 casa com "0,4275", "0,42750" ... (zeros à direita são opcionais)
    integer, _, frac = f"{value.normalize():f}".partition(".")
    integer = f"{int(integer):,}".replace(",", r"\.")
    return [rf"\b{integer},{frac}0*\b" if frac else rf"\b{integer}(,0+)?\b"]


def count_mentions(text: str, value: str, kind: str) -> int:
    """Quantas vezes o valor aparece no texto, em qualquer formato usual (kind: 'data' | 'valor')."""
    norm_text = normalize(text)
    if kind == "data":
        patterns = _date_patterns(date.fromisoformat(value))
    elif kind == "valor":
        patterns = _money_patterns(Decimal(value))
    else:
        patterns = [re.escape(normalize(value))]
    return sum(len(re.findall(p, norm_text)) for p in patterns)
