"""Validação de emissor/ISIN/ticker/CNPJ contra golden_records.csv."""

import csv
import re
import unicodedata
from functools import cache

from case_btg.config import GOLDEN_PATH

TICKER_CLASS = {"3": "ON", "4": "PN", "5": "PNA", "6": "PNB", "11": "UNIT"}


@cache
def _records() -> tuple[dict, ...]:
    with GOLDEN_PATH.open(encoding="utf-8") as f:
        return tuple(csv.DictReader(f))


def _norm_name(name: str) -> str:
    name = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    name = re.sub(r"\bS\.?\s*/?\s*A\.?", "", name.upper())
    return re.sub(r"[^A-Z0-9]", "", name)


def _digits(s: str) -> str:
    return re.sub(r"\D", "", s)


def isin_check_digit_ok(isin: str) -> bool:
    if not re.fullmatch(r"[A-Z]{2}[A-Z0-9]{9}\d", isin):
        return False
    digits = "".join(str(int(c, 36)) for c in isin[:-1])
    total = 0
    for i, ch in enumerate(reversed(digits)):
        n = int(ch) * (2 if i % 2 == 0 else 1)
        total += n - 9 if n > 9 else n
    return (10 - total % 10) % 10 == int(isin[-1])


def lookup_golden_record(
    isin: str | None = None,
    ticker: str | None = None,
    cnpj: str | None = None,
    emissor: str | None = None,
) -> dict:
    """Procura cada identificador na base e diz se todos apontam para o mesmo registro."""
    keys = {
        "isin": (isin, lambda r: r["isin"] == isin.strip().upper()),
        "ticker": (ticker, lambda r: r["ticker"] == ticker.strip().upper()),
        "cnpj": (cnpj, lambda r: _digits(r["cnpj"]) == _digits(cnpj)),
        "emissor": (emissor, lambda r: _norm_name(r["emissor"]) == _norm_name(emissor)),
    }
    matches: dict[str, dict | None] = {}
    for key, (value, pred) in keys.items():
        if value:
            matches[key] = next((r for r in _records() if pred(r)), None)

    found = {k: r["isin"] for k, r in matches.items() if r}
    not_found = [k for k, r in matches.items() if r is None]
    distinct = set(found.values())

    if not found:
        status = "nao_encontrado"
    elif len(distinct) > 1:
        status = "conflito"
    elif not_found:
        status = "parcial"
    else:
        status = "ok"

    record = matches[next(iter(found))] if len(distinct) == 1 else None
    return {
        "status": status,
        "registro": record,
        "chaves_encontradas": sorted(found),
        "chaves_nao_encontradas": not_found,
        "isin_por_chave": found,
        "isin_digito_verificador_ok": isin_check_digit_ok(isin) if isin else None,
        "classe_pelo_ticker": TICKER_CLASS.get(re.sub(r"^[A-Z]{4}", "", ticker or "")),
    }
