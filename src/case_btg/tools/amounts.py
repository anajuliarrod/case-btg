"""Parsing de valores em formato brasileiro e coerência bruto/IR/líquido e proporções."""

import re
from decimal import Decimal, InvalidOperation
from fractions import Fraction


def parse_brl(text: str) -> Decimal:
    """'R$ 0,4275000000' -> Decimal('0.4275000000'). Mantém as casas decimais do documento."""
    cleaned = re.sub(r"[^\d,.-]", "", text)
    if "," in cleaned:
        cleaned = cleaned.replace(".", "").replace(",", ".")
    try:
        return Decimal(cleaned)
    except InvalidOperation as e:
        raise ValueError(f"valor monetário inválido: {text!r}") from e


def parse_percent(text: str) -> Decimal:
    """'17,5%' -> Decimal('0.175')."""
    return parse_brl(text.replace("%", "")) / 100


def check_gross_net(valor_bruto: str, aliquota_ir: str, valor_liquido: str) -> dict:
    """Confere líquido = bruto × (1 − alíquota), com tolerância de meia unidade na última casa do líquido."""
    bruto, aliquota, liquido = parse_brl(valor_bruto), parse_percent(aliquota_ir), parse_brl(valor_liquido)
    expected = bruto * (1 - aliquota)
    tolerance = Decimal(1).scaleb(liquido.as_tuple().exponent) / 2
    ok = abs(expected - liquido) <= tolerance
    return {
        "regra": "valores.bruto_liquido_ir",
        "resultado": "ok" if ok else "falha",
        "severidade": "bloqueante",
        "detalhe": f"{bruto} × (1 − {aliquota}) = {expected.normalize()}; informado {liquido}",
    }


def parse_ratio(text: str) -> Fraction:
    """'10:1' ou '1 para 20' -> Fraction(antes, depois)."""
    nums = re.findall(r"\d+", text)
    if len(nums) < 2:
        raise ValueError(f"proporção inválida: {text!r}")
    return Fraction(int(nums[0]), int(nums[1]))


def check_ratio_percent(acoes_novas: int, acoes_base: int, percentual: str) -> dict:
    """Bonificação: '1 nova para cada 20' deve bater com '5%'."""
    expected = Decimal(acoes_novas) / Decimal(acoes_base)
    informed = parse_percent(percentual)
    return {
        "regra": "valores.proporcao_vs_percentual",
        "resultado": "ok" if expected == informed else "falha",
        "severidade": "bloqueante",
        "detalhe": f"{acoes_novas}/{acoes_base} = {expected:.4%}; informado {informed:.4%}",
    }
