"""Gate determinístico: reexecuta as validações sobre a extração do LLM, atribui confiança
por campo e decide o roteamento. Nada aqui depende do que o LLM diz sobre a própria confiança."""

import re
from datetime import date
from decimal import InvalidOperation

from case_btg.ingest import Document
from case_btg.schema import (
    Auditoria,
    Campo,
    CampoExtraido,
    DocumentoInfo,
    Evento,
    EventoExtraido,
    Evidencia,
    Extracao,
    ItemRevisao,
    Registro,
    Verificacao,
)
from case_btg.tools.amounts import check_gross_net, check_ratio_percent, parse_ratio
from case_btg.tools.dates import validate_dates
from case_btg.tools.evidence import count_mentions, locate_quote, normalize
from case_btg.tools.golden import lookup_golden_record

IDENT_FIELDS = ["emissor", "cnpj", "isin", "ticker", "classe_acao"]
IDENT_CRITICAL = {"emissor", "isin", "ticker"}
EVENT_FIELDS = [
    "data_aprovacao", "data_com", "data_ex", "data_pagamento", "moeda", "valor_bruto",
    "aliquota_ir", "valor_liquido", "proporcao", "percentual_bonificacao", "custo_atribuido",
]
DATE_FIELDS = {"data_aprovacao", "data_com", "data_ex", "data_pagamento", "data_documento"}
NUMERIC_FIELDS = {"valor_bruto", "valor_liquido", "custo_atribuido", "aliquota_ir", "percentual_bonificacao"}

_CASH = {"data_aprovacao", "data_com", "data_ex", "data_pagamento", "moeda", "valor_bruto", "aliquota_ir", "valor_liquido"}
_RATIO = {"data_aprovacao", "data_com", "data_ex", "proporcao"}
APPLICABLE = {
    "dividendo": _CASH,
    "jcp": _CASH,
    "bonificacao": _RATIO | {"data_pagamento", "moeda", "percentual_bonificacao", "custo_atribuido"},
    "grupamento": _RATIO,
    "desdobramento": _RATIO,
    "outro": set(EVENT_FIELDS),
}
CRITICAL = {
    "dividendo": {"data_com", "data_ex", "data_pagamento", "moeda", "valor_bruto"},
    "jcp": {"data_com", "data_ex", "data_pagamento", "moeda", "valor_bruto", "aliquota_ir", "valor_liquido"},
    "bonificacao": {"data_com", "data_ex", "proporcao", "custo_atribuido"},
    "grupamento": {"data_com", "data_ex", "proporcao"},
    "desdobramento": {"data_com", "data_ex", "proporcao"},
    "outro": set(),
}
UNITS = {
    "valor_bruto": "BRL/acao", "valor_liquido": "BRL/acao", "custo_atribuido": "BRL/acao",
    "aliquota_ir": "%", "percentual_bonificacao": "%",
}
RATIO_UNIT = {"bonificacao": "novas:base", "grupamento": "antes:depois", "desdobramento": "antes:depois"}

DATE_RULE_FIELDS = {
    "datas.com_dia_util": ["data_com"],
    "datas.com_antes_ex": ["data_com", "data_ex"],
    "datas.ex_proximo_dia_util": ["data_com", "data_ex"],
    "datas.aprovacao_ate_com": ["data_aprovacao", "data_com"],
    "datas.aprovacao_ate_documento": ["data_aprovacao"],
    "datas.pagamento_apos_com": ["data_pagamento", "data_com"],
    "datas.pagamento_apos_ex": ["data_pagamento", "data_ex"],
    "datas.pagamento_ausente": ["data_pagamento"],
}
# Regras que confirmam o valor contra uma fonte independente (podem elevar leitura de imagem a 'alta').
CROSS_CHECKS = {"golden.identificacao", "golden.classe", "valores.bruto_liquido_ir",
                "valores.proporcao_vs_percentual", "valores.moeda_vs_isin", "datas.ex_proximo_dia_util"}

# Ordem importa: o primeiro padrão que casa decide. "dividendo" é o sinal mais fraco porque JCP
# costuma ser "imputado aos dividendos obrigatórios".
KEYWORDS = [
    ("grupamento", r"\bgrupamento\b|\binplit\b|\breverse split\b"),
    ("desdobramento", r"\bdesdobramento\b|\bsplit\b"),
    ("bonificacao", r"\bbonifica"),
    ("jcp", r"capital proprio|\bjcp\b|9\.249"),
    ("dividendo", r"\bdividendo"),
]
QUOTE_NOT_FOUND = "trecho citado não foi localizado no PDF (possível alucinação)"

# Classe pelo nome, pela sigla, pelo sufixo do ticker B3 (3 = ON, 4 = PN) ou pelo trecho do ISIN.
CLASS_PATTERNS = {
    "ON": r"\bordinaria|\bon\b|\b[a-z]{4}3\b|acnor",
    "PN": r"\bpreferencia|\bpn\b|\b[a-z]{4}4\b|acnpr",
}
TYPE_LABEL = {"dividendo": "Dividendo", "jcp": "JCP", "bonificacao": "Bonificação",
              "grupamento": "Grupamento", "desdobramento": "Desdobramento", "outro": "Evento"}
ACTIONS = {
    "golden": "Confirmar identificação do ativo em fonte oficial (B3/CVM) e atualizar a base de referência",
    "datas": "Confirmar as datas com a área de RI do emissor; não processar até corrigir",
    "valores": "Recalcular a partir do documento original; não processar até corrigir",
    "classificacao": "Confirmar a natureza do evento: o tipo define o tratamento tributário",
}


def classify_by_keywords(text: str) -> str | None:
    norm = normalize(text)
    return next((tipo for tipo, pattern in KEYWORDS if re.search(pattern, norm)), None)


def _br(number: str) -> str:
    return number.replace(".", ",")


def _fmt(iso: str | None) -> str:
    return date.fromisoformat(iso).strftime("%d/%m/%Y") if iso else "não informada"


# ---------------------------------------------------------------- avaliação de campo


def _value_matches(name: str, valor: str, trecho: str) -> tuple[bool, str]:
    """O valor normalizado pelo LLM corresponde ao que está escrito no trecho citado?"""
    try:
        if name in DATE_FIELDS:
            ok = count_mentions(trecho, valor, "data") > 0
        elif name in NUMERIC_FIELDS:
            ok = count_mentions(trecho, valor, "valor") > 0
        elif name == "proporcao":
            ok = set(re.findall(r"\d+", valor)) <= set(re.findall(r"\d+", trecho))
        elif name == "cnpj":
            ok = re.sub(r"\D", "", valor) in re.sub(r"\D", "", trecho)
        elif name == "classe_acao":
            ok = re.search(CLASS_PATTERNS.get(valor.upper(), r"(?!x)x"), normalize(trecho)) is not None
        elif name == "moeda":
            ok = valor != "BRL" or "r$" in normalize(trecho) or "rea" in normalize(trecho)
        else:
            ok = re.search(rf"(?<!\w){re.escape(normalize(valor))}(?!\w)", normalize(trecho)) is not None
    except (ValueError, InvalidOperation):
        return False, f"valor em formato inválido: {valor!r}"
    return ok, "" if ok else f"valor {valor!r} não aparece no trecho citado"


def _assess(name: str, ext: CampoExtraido, doc: Document, *, applicable: bool, critical: bool,
            unit: str | None = None) -> Campo:
    evid = None
    if ext.trecho:
        if doc.source == "texto_nativo":
            loc = locate_quote(doc.text, ext.trecho)
            evid = Evidencia(trecho=ext.trecho, pagina=ext.pagina, linha=loc["linha"], verificada=loc["encontrado"])
        else:
            evid = Evidencia(trecho=ext.trecho, pagina=ext.pagina, verificada=None)

    if ext.valor is None:
        if not applicable:
            return Campo(valor=None, status="nao_aplicavel", confianca=None)
        if evid and evid.verificada is not False:
            return Campo(valor=None, unidade=unit, status="ausente_no_documento",
                         confianca="alta" if evid.verificada else "media",
                         motivos=["o documento declara que a informação não está disponível"], evidencia=evid)
        return Campo(valor=None, unidade=unit, status="nao_encontrado", confianca="baixa" if critical else None,
                     motivos=["não localizado no documento"] if critical else [])

    if not applicable:
        return Campo(valor=ext.valor, unidade=unit, status="extraido", confianca="baixa", evidencia=evid,
                     motivos=["campo não se aplica a este tipo de evento: possível valor no campo errado"])

    if evid is None:
        return Campo(valor=ext.valor, unidade=unit, status="extraido", confianca="baixa",
                     motivos=["extração sem trecho de origem"])
    if evid.verificada is False:
        return Campo(valor=ext.valor, unidade=unit, status="extraido", confianca="baixa", evidencia=evid,
                     motivos=[QUOTE_NOT_FOUND])
    matches, why = _value_matches(name, ext.valor, ext.trecho)
    if not matches:
        return Campo(valor=ext.valor, unidade=unit, status="extraido", confianca="baixa", evidencia=evid,
                     motivos=[why])

    if evid.verificada is None:
        return Campo(valor=ext.valor, unidade=unit, status="extraido", confianca="media", evidencia=evid,
                     motivos=["lido de imagem (PDF sem camada de texto): trecho não verificável"])

    motivos = [f"trecho localizado no PDF (pág. {evid.pagina or 1}, linha {evid.linha})"]
    if name in DATE_FIELDS | NUMERIC_FIELDS:
        n = count_mentions(doc.text, ext.valor, "data" if name in DATE_FIELDS else "valor")
        if n >= 2:
            motivos.append(f"valor aparece {n}x no documento (corpo e tabela concordam)")
    return Campo(valor=ext.valor, unidade=unit, status="extraido", confianca="alta", evidencia=evid, motivos=motivos)


def _apply_checks(fields: dict[str, Campo], checks: list[Verificacao], scan: bool) -> None:
    """Ordem: confirmações, depois 'não validado', depois falhas, para que a pior evidência prevaleça."""
    for c in checks:
        if c.resultado == "ok" and c.regra in CROSS_CHECKS:
            for path in c.campos:
                f = fields.get(path)
                if f and f.valor is not None:
                    if f.evidencia and f.evidencia.verificada is False and f.confianca == "baixa":
                        # valor certo, evidência inválida: o operador não consegue auditar pelo trecho
                        f.confianca = "media"
                        f.motivos = [m for m in f.motivos if m != QUOTE_NOT_FOUND]
                        f.motivos.append(f"valor confirmado por {c.regra}, mas a citação não é literal "
                                         "(trecho não existe no PDF): evidência não auditável")
                        continue
                    f.motivos.append(f"confirmado por {c.regra}")
                    if scan and f.confianca == "media":
                        f.confianca = "alta"
    for c in checks:
        for path in c.campos:
            f = fields.get(path)
            if not f or f.valor is None:
                continue
            if c.resultado == "nao_validado" and f.confianca == "alta":
                f.confianca = "media"
                f.motivos.append(f"não validável: {c.detalhe}")
            elif c.resultado == "conflito" and f.confianca == "alta":
                f.confianca = "media"
                f.motivos.append(f"conflito em {c.regra}: {c.detalhe}")
    for c in checks:
        if c.resultado != "falha" or c.severidade == "info":
            continue
        for path in c.campos:
            f = fields.get(path)
            if f and f.valor is not None:
                f.confianca = "baixa"
                f.motivos.append(f"falhou em {c.regra}: {c.detalhe}")


# ---------------------------------------------------------------- verificações


def _golden_checks(ident: dict[str, Campo]) -> tuple[list[Verificacao], dict]:
    values = {k: ident[k].valor for k in ("isin", "ticker", "cnpj", "emissor")}
    r = lookup_golden_record(**values)
    provided = [f"identificacao.{k}" for k, v in values.items() if v]
    checks: list[Verificacao] = []

    if r["status"] == "ok":
        checks.append(Verificacao(regra="golden.identificacao", resultado="ok", severidade="bloqueante",
                                  detalhe=f"{', '.join(r['chaves_encontradas'])} apontam para {r['registro']['isin']}",
                                  campos=provided))
    elif r["status"] == "nao_encontrado":
        checks.append(Verificacao(regra="golden.identificacao", resultado="nao_validado", severidade="revisao",
                                  detalhe="emissor/ativo não consta na base de referência", campos=provided))
    elif r["status"] == "conflito":
        checks.append(Verificacao(regra="golden.identificacao", resultado="falha", severidade="bloqueante",
                                  detalhe=f"identificadores apontam para ativos diferentes: {r['isin_por_chave']}",
                                  campos=provided))
    else:
        found = [f"identificacao.{k}" for k in r["chaves_encontradas"]]
        checks.append(Verificacao(regra="golden.identificacao", resultado="ok", severidade="bloqueante",
                                  detalhe=f"{', '.join(r['chaves_encontradas'])} conferem", campos=found))
        for k in r["chaves_nao_encontradas"]:
            checks.append(Verificacao(regra=f"golden.{k}", resultado="falha", severidade="revisao",
                                      detalhe=f"{k} {values[k]!r} diverge da base ({r['registro'][k]!r})",
                                      campos=[f"identificacao.{k}"]))

    classe = ident["classe_acao"].valor
    if r["registro"] and classe:
        ok = classe.upper() == r["registro"]["classe"]
        checks.append(Verificacao(regra="golden.classe", resultado="ok" if ok else "falha", severidade="revisao",
                                  detalhe=f"documento {classe}, base {r['registro']['classe']}",
                                  campos=["identificacao.classe_acao"]))
    if r["isin_digito_verificador_ok"] is False:
        checks.append(Verificacao(regra="isin.digito_verificador", resultado="falha", severidade="info",
                                  detalhe="dígito verificador ISO 6166 não confere (esperado no lote sintético)"))
    return checks, r


def _iso_or_none(c: Campo) -> str | None:
    try:
        return date.fromisoformat(c.valor).isoformat() if c.valor else None
    except ValueError:
        return None


def _event_checks(i: int, tipo: str, ev: EventoExtraido, f: dict[str, Campo], data_documento: str | None,
                  keyword_tipo: str | None, isin: str | None) -> list[Verificacao]:
    p = f"eventos[{i}]"
    checks: list[Verificacao] = []

    for c in validate_dates(
        tipo, _iso_or_none(f["data_aprovacao"]), _iso_or_none(f["data_com"]), _iso_or_none(f["data_ex"]),
        _iso_or_none(f["data_pagamento"]), data_documento,
    ):
        campos = [f"{p}.{n}" for n in DATE_RULE_FIELDS[c["regra"]]]
        if c["regra"] == "datas.aprovacao_ate_documento":
            campos.append("data_documento")
        checks.append(Verificacao(**c, campos=campos))

    moeda = f["moeda"].valor
    if moeda and isin:
        ok = (moeda == "BRL") == isin.upper().startswith("BR")
        checks.append(Verificacao(regra="valores.moeda_vs_isin", resultado="ok" if ok else "conflito",
                                  severidade="revisao", detalhe=f"moeda {moeda}, ISIN {isin[:2]}",
                                  campos=[f"{p}.moeda"]))

    bruto, aliq, liq = f["valor_bruto"].valor, f["aliquota_ir"].valor, f["valor_liquido"].valor
    try:
        if bruto and aliq and liq:
            checks.append(Verificacao(**check_gross_net(bruto, aliq, liq),
                                      campos=[f"{p}.valor_bruto", f"{p}.aliquota_ir", f"{p}.valor_liquido"]))
        prop, pct = f["proporcao"].valor, f["percentual_bonificacao"].valor
        if tipo == "bonificacao" and prop and pct:
            ratio = parse_ratio(prop)
            checks.append(Verificacao(**check_ratio_percent(ratio.numerator, ratio.denominator, pct),
                                      campos=[f"{p}.proporcao", f"{p}.percentual_bonificacao"]))
        if tipo in {"grupamento", "desdobramento"} and prop:
            antes, depois = (int(n) for n in re.findall(r"\d+", prop)[:2])
            ok = antes > depois if tipo == "grupamento" else antes < depois
            checks.append(Verificacao(regra="classificacao.proporcao_vs_tipo", resultado="ok" if ok else "falha",
                                      severidade="bloqueante", detalhe=f"{tipo} com proporção {prop}",
                                      campos=[f"{p}.proporcao", f"{p}.tipo"]))
    except (ValueError, InvalidOperation, ZeroDivisionError) as e:
        checks.append(Verificacao(regra="valores.formato", resultado="falha", severidade="bloqueante",
                                  detalhe=str(e), campos=[f"{p}.proporcao"]))

    if keyword_tipo is None:
        checks.append(Verificacao(regra="classificacao.palavras_chave", resultado="nao_validado", severidade="revisao",
                                  detalhe="nenhum sinal determinístico de tipo no texto", campos=[f"{p}.tipo"]))
    else:
        ok = keyword_tipo == tipo
        checks.append(Verificacao(regra="classificacao.palavras_chave", resultado="ok" if ok else "falha",
                                  severidade="bloqueante",
                                  detalhe=f"LLM: {tipo}; regras por palavra-chave: {keyword_tipo}",
                                  campos=[f"{p}.tipo"]))

    label = ev.rotulo_documento.trecho or ev.rotulo_documento.valor
    label_tipo = classify_by_keywords(label) if label else None
    if label_tipo and label_tipo != tipo:
        checks.append(Verificacao(regra="classificacao.rotulo_vs_conteudo", resultado="conflito",
                                  severidade="revisao",
                                  detalhe=f"título indica '{label_tipo}', conteúdo indica '{tipo}' ({label!r})",
                                  campos=[f"{p}.tipo"]))

    if tipo == "jcp" and not aliq:
        checks.append(Verificacao(regra="classificacao.tributacao_coerente", resultado="falha", severidade="revisao",
                                  detalhe="JCP sem alíquota de IRRF informada", campos=[f"{p}.aliquota_ir"]))
    if tipo == "dividendo" and aliq and liq:
        checks.append(Verificacao(regra="classificacao.tributacao_coerente", resultado="conflito",
                                  severidade="revisao",
                                  detalhe="dividendo com IR retido uniformemente por ação é padrão de JCP",
                                  campos=[f"{p}.tipo"]))
    return checks


def _effect(tipo: str, f: dict[str, Campo]) -> str | None:
    bruto, liq, prop = f["valor_bruto"].valor, f["valor_liquido"].valor, f["proporcao"].valor
    if tipo in {"dividendo", "jcp"} and bruto:
        text = f"Crédito em dinheiro: R$ {_br(bruto)} bruto por ação"
        if liq:
            text += f", R$ {_br(liq)} líquido após IRRF de {_br(f['aliquota_ir'].valor or '?')}%"
        elif tipo == "dividendo":
            text += "; líquido depende do beneficiário (sem alíquota uniforme por ação)"
        return text
    nums = [int(n) for n in re.findall(r"\d+", prop or "")[:2]]
    if len(nums) < 2 or 0 in nums:
        return None
    a, b = nums
    if tipo == "bonificacao":
        text = f"Crédito de ações: {a} nova(s) para cada {b} detidas (quantidade × {b + a}/{b})"
        if f["custo_atribuido"].valor:
            text += f"; custo atribuído R$ {_br(f['custo_atribuido'].valor)} por ação nova"
        return text
    if tipo in {"grupamento", "desdobramento"}:
        return f"Ajuste de quantidade: cada {a} ação(ões) vira(m) {b} (quantidade × {b}/{a}); sem crédito em dinheiro"
    return None


# ---------------------------------------------------------------- roteamento


def _field_review(path: str, f: Campo, critical: bool, scan: bool) -> ItemRevisao | None:
    if f.status == "nao_encontrado" and critical:
        return ItemRevisao(campo=path, severidade="bloqueante", motivo="campo obrigatório não localizado",
                           acao_sugerida="Localizar no documento original ou solicitar aviso complementar")
    if f.confianca == "baixa" or (critical and f.confianca == "media"):
        acao = ("Conferir o valor na imagem do documento original" if scan and f.confianca == "media"
                else "Conferir no documento original antes de processar")
        return ItemRevisao(campo=path, severidade="revisao" if f.confianca == "media" else "bloqueante",
                           motivo="; ".join(f.motivos) or "baixa confiança", acao_sugerida=acao)
    return None


def _summary(registro_status: str, ident: dict[str, Campo], eventos: list[Evento], n_review: int) -> str:
    ticker = ident["ticker"].valor or ident["emissor"].valor or "ativo não identificado"
    parts = []
    for ev in eventos:
        c = ev.campos
        dates = f"Data com {_fmt(c['data_com'].valor)}, ex {_fmt(c['data_ex'].valor)}"
        if c["data_pagamento"].status != "nao_aplicavel":
            dates += f", pagamento/crédito {_fmt(c['data_pagamento'].valor)}"
        parts.append(f"{TYPE_LABEL[ev.tipo.valor]} {ticker}. {ev.efeito or 'efeito não determinado'}. {dates}.")
    tail = {"aprovado": "Aprovado.",
            "aprovado_com_ressalvas": "Aprovado com pendências (ver 'pendencias').",
            "revisao_humana": f"Requer revisão humana: {n_review} item(ns)."}[registro_status]
    return " ".join(parts + [tail]) if parts else f"Nenhum evento identificado. {tail}"


def evaluate(doc: Document, ext: Extracao, auditoria: Auditoria) -> Registro:
    scan = doc.source == "imagem_escaneada"
    fields: dict[str, Campo] = {}
    critical_paths: set[str] = set()

    ident = {n: _assess(n, getattr(ext.identificacao, n), doc, applicable=True, critical=n in IDENT_CRITICAL)
             for n in IDENT_FIELDS}
    fields |= {f"identificacao.{n}": c for n, c in ident.items()}
    critical_paths |= {f"identificacao.{n}" for n in IDENT_CRITICAL}
    data_doc = _assess("data_documento", ext.data_documento, doc, applicable=True, critical=False)
    fields["data_documento"] = data_doc

    checks, golden = _golden_checks(ident)
    body = doc.text if not scan else (ext.transcricao or "")
    keyword_tipo = classify_by_keywords(body) if body.strip() else None

    event_fields: list[tuple[EventoExtraido, Campo, dict[str, Campo]]] = []
    for i, ev in enumerate(ext.eventos):
        tipo = ev.tipo_evento
        f = {n: _assess(n, getattr(ev, n), doc, applicable=n in APPLICABLE[tipo], critical=n in CRITICAL[tipo],
                        unit=RATIO_UNIT.get(tipo) if n == "proporcao" else UNITS.get(n))
             for n in EVENT_FIELDS}
        tipo_campo = Campo(valor=tipo, status="extraido", confianca="alta" if keyword_tipo else "media",
                           motivos=[f"LLM: {ev.justificativa_tipo}"])
        fields |= {f"eventos[{i}].{n}": c for n, c in f.items()} | {f"eventos[{i}].tipo": tipo_campo}
        critical_paths |= {f"eventos[{i}].{n}" for n in CRITICAL[tipo]} | {f"eventos[{i}].tipo"}
        checks += _event_checks(i, tipo, ev, f, _iso_or_none(data_doc), keyword_tipo, ident["isin"].valor)
        event_fields.append((ev, tipo_campo, f))

    _apply_checks(fields, checks, scan)

    review: list[ItemRevisao] = []
    covered: set[str] = set()
    for c in checks:
        if c.resultado in {"falha", "conflito", "nao_validado"} and c.severidade != "info":
            review.append(ItemRevisao(campo=", ".join(c.campos) or c.regra, severidade=c.severidade,
                                      motivo=f"{c.regra}: {c.detalhe}",
                                      acao_sugerida=ACTIONS.get(c.regra.split(".")[0], "Verificar no documento")))
            covered |= set(c.campos)
    for path, f in fields.items():
        if path not in covered and (item := _field_review(path, f, path in critical_paths, scan)):
            review.append(item)
    if not ext.eventos:
        review.append(ItemRevisao(campo="eventos", severidade="bloqueante", motivo="nenhum evento identificado",
                                  acao_sugerida="Classificar e extrair manualmente"))

    pendencias = [
        f"{path}: não informado no aviso (\"{f.evidencia.trecho}\")"
        for path, f in fields.items()
        if f.status == "ausente_no_documento" and path in critical_paths and f.evidencia
    ]
    status = "revisao_humana" if review else "aprovado_com_ressalvas" if pendencias else "aprovado"

    eventos = [
        Evento(tipo=tipo_campo, rotulo_documento=ev.rotulo_documento.trecho, efeito=_effect(ev.tipo_evento, f),
               campos=f, observacoes=ev.observacoes)
        for ev, tipo_campo, f in event_fields
    ]
    return Registro(
        documento=DocumentoInfo(arquivo=doc.path.name, sha256=doc.sha256, paginas=len(doc.pages), fonte=doc.source),
        status=status,
        resumo_operador=_summary(status, ident, eventos, len(review)),
        identificacao=ident,
        data_documento=data_doc,
        validacao_golden={"status": golden["status"], "registro": golden["registro"],
                          "chaves_encontradas": golden["chaves_encontradas"],
                          "chaves_nao_encontradas": golden["chaves_nao_encontradas"]},
        eventos=eventos,
        revisao_humana=review,
        pendencias=pendencias,
        verificacoes=checks,
        auditoria=auditoria,
    )
