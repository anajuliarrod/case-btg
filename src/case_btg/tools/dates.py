"""Regras de coerência de datas por tipo de evento, com calendário B3."""

from datetime import date, timedelta

# Feriados sem pregão na B3 em 2026 (Páscoa em 05/04).
B3_HOLIDAYS = {
    date(2026, 1, 1),
    date(2026, 2, 16),
    date(2026, 2, 17),
    date(2026, 4, 3),
    date(2026, 4, 21),
    date(2026, 5, 1),
    date(2026, 6, 4),
    date(2026, 9, 7),
    date(2026, 10, 12),
    date(2026, 11, 2),
    date(2026, 11, 20),
    date(2026, 12, 24),
    date(2026, 12, 25),
    date(2026, 12, 31),
}


def is_business_day(d: date) -> bool:
    return d.weekday() < 5 and d not in B3_HOLIDAYS


def next_business_day(d: date) -> date:
    d += timedelta(days=1)
    while not is_business_day(d):
        d += timedelta(days=1)
    return d


def _check(rule: str, ok: bool, severity: str, detail: str) -> dict:
    return {"regra": rule, "resultado": "ok" if ok else "falha", "severidade": severity, "detalhe": detail}


def validate_dates(
    tipo_evento: str,
    data_aprovacao: str | None = None,
    data_com: str | None = None,
    data_ex: str | None = None,
    data_pagamento: str | None = None,
    data_documento: str | None = None,
) -> list[dict]:
    """Datas em ISO (AAAA-MM-DD). Para grupamento/bonificação, data_ex = início da negociação ajustada
    e data_pagamento = crédito das ações (quando houver). Regras sem dados suficientes são puladas."""
    d = {
        k: date.fromisoformat(v) if v else None
        for k, v in {
            "aprovacao": data_aprovacao,
            "com": data_com,
            "ex": data_ex,
            "pagamento": data_pagamento,
            "documento": data_documento,
        }.items()
    }
    checks: list[dict] = []
    add = checks.append

    if d["com"]:
        add(_check("datas.com_dia_util", is_business_day(d["com"]), "revisao",
                   f"data com {d['com']} ({d['com']:%a})"))
    if d["com"] and d["ex"]:
        expected = next_business_day(d["com"])
        add(_check("datas.com_antes_ex", d["com"] < d["ex"], "bloqueante", f"com {d['com']} < ex {d['ex']}"))
        add(_check("datas.ex_proximo_dia_util", d["ex"] == expected, "revisao",
                   f"esperado {expected}, informado {d['ex']}"))
    if d["aprovacao"] and d["com"]:
        add(_check("datas.aprovacao_ate_com", d["aprovacao"] <= d["com"], "bloqueante",
                   f"aprovação {d['aprovacao']} <= com {d['com']}"))
    if d["aprovacao"] and d["documento"]:
        add(_check("datas.aprovacao_ate_documento", d["aprovacao"] <= d["documento"], "revisao",
                   f"aprovação {d['aprovacao']} <= documento {d['documento']}"))
    if d["pagamento"] and d["com"]:
        add(_check("datas.pagamento_apos_com", d["pagamento"] > d["com"], "bloqueante",
                   f"pagamento {d['pagamento']} > com {d['com']}"))
    if d["pagamento"] and d["ex"]:
        add(_check("datas.pagamento_apos_ex", d["pagamento"] >= d["ex"], "revisao",
                   f"pagamento {d['pagamento']} >= ex {d['ex']}"))
    if tipo_evento in {"dividendo", "jcp"} and d["com"] and not d["pagamento"]:
        add({"regra": "datas.pagamento_ausente", "resultado": "pendente", "severidade": "info",
             "detalhe": "data de pagamento não informada no aviso"})
    return checks
