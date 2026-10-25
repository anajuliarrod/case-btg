"""Gate com extrações escritas à mão, simulando o LLM (correto e errando)."""

from case_btg.config import INPUT_DIR
import pytest

from case_btg.gate import _value_matches, evaluate
from case_btg.ingest import load_document
from case_btg.schema import Auditoria, CampoExtraido, EventoExtraido, Extracao, IdentificacaoExtraida

AUD = Auditoria(modelo="fixture", prompt_versao="test", processado_em="2026-10-21T00:00:00")
EVENT_FIELDS = list(EventoExtraido.model_fields)


def F(valor=None, trecho=None):
    return CampoExtraido(valor=valor, trecho=trecho, pagina=1 if trecho else None)


def extraction(ident, data_doc, tipo, rotulo, transcricao=None, **campos):
    ev = {n: F() for n in EVENT_FIELDS if n not in {"tipo_evento", "justificativa_tipo", "observacoes"}}
    ev |= {k: F(*v) for k, v in campos.items()} | {"rotulo_documento": F(None, rotulo)}
    return Extracao(
        identificacao=IdentificacaoExtraida(**{k: F(*v) for k, v in ident.items()}),
        data_documento=F(*data_doc),
        eventos=[EventoExtraido(tipo_evento=tipo, justificativa_tipo="fixture", **ev)],
        transcricao=transcricao,
    )


def run(filename, ext):
    return evaluate(load_document(INPUT_DIR / filename), ext, AUD)


def paths(reg):
    return {item.campo for item in reg.revisao_humana}


IDENT_TIET = {
    "emissor": ("Energética Vale do Tietê S.A.", "A Energética Vale do Tietê S.A."),
    "cnpj": ("12.345.678/0001-90", "CNPJ/ME nº 12.345.678/0001-90"),
    "isin": ("BRTIETACNOR3", "TIET3 (ISIN BRTIETACNOR3)"),
    "ticker": ("TIET3", "TIET3 (ISIN BRTIETACNOR3)"),
    "classe_acao": ("ON", "R$ 0,4275000000 (ON)"),
}

IDENT_BMRD = {
    "emissor": ("Banco Meridional do Brasil S.A.", "O Banco Meridional do Brasil S.A."),
    "cnpj": ("60.111.222/0001-55", "CNPJ/ME nº 60.111.222/0001-55"),
    "isin": ("BRBMRDACNPR7", "BMRD4 (ISIN BRBMRDACNPR7)"),
    "ticker": ("BMRD4", "BMRD4 (ISIN BRBMRDACNPR7)"),
    "classe_acao": ("PN", "Valor bruto por ação preferencial R$ 0,1738420000 (PN)"),
}
JCP_BMRD = dict(
    data_aprovacao=("2026-06-02", "em reunião realizada em 02 de junho de 2026"),
    data_com=("2026-06-16", "Data-base (“data com”) 16/06/2026"),
    data_ex=("2026-06-17", "Data “ex-JCP” 17/06/2026"),
    data_pagamento=("2026-08-14", "Data de pagamento 14/08/2026"),
    moeda=("BRL", "R$ 0,1738420000"),
    valor_bruto=("0.1738420000", "R$ 0,1738420000"),
    aliquota_ir=("17.5", "IRRF na fonte 17,5%"),
    valor_liquido=("0.1434196500", "Valor líquido por ação (PN) R$ 0,1434196500"),
)


def test_doc01_clean_dividend_is_approved():
    reg = run("01_energetica_vale_tiete_dividendo.pdf", extraction(
        IDENT_TIET, ("2026-05-28", "São Paulo (SP), 28 de maio de 2026."), "dividendo",
        "AVISO AOS ACIONISTAS — Pagamento de Dividendos",
        data_aprovacao=("2026-05-28", "Data de aprovação (RCA) 28/05/2026"),
        data_com=("2026-06-12", "Data-base (“data com”) 12/06/2026"),
        data_ex=("2026-06-15", "Data “ex-dividendos” 15/06/2026"),
        data_pagamento=("2026-07-03", "Data de pagamento 03/07/2026"),
        moeda=("BRL", "R$ 0,4275000000"),
        valor_bruto=("0.4275000000", "R$ 0,4275000000"),
    ))
    assert reg.status == "aprovado", reg.revisao_humana
    campos = reg.eventos[0].campos
    assert campos["data_com"].confianca == "alta"
    assert any("2x" in m for m in campos["data_com"].motivos)
    assert campos["proporcao"].status == "nao_aplicavel"
    assert "líquido depende do beneficiário" in reg.eventos[0].efeito


def test_doc02_clean_jcp_is_approved():
    reg = run("02_banco_meridional_jcp.pdf", extraction(
        IDENT_BMRD, ("2026-06-02", "São Paulo (SP), 02 de junho de 2026."), "jcp",
        "AVISO AOS ACIONISTAS — Juros sobre o Capital Próprio (JCP)", **JCP_BMRD,
    ))
    assert reg.status == "aprovado", reg.revisao_humana


def test_jcp_misclassified_as_dividend_is_caught():
    reg = run("02_banco_meridional_jcp.pdf", extraction(
        IDENT_BMRD, ("2026-06-02", "São Paulo (SP), 02 de junho de 2026."), "dividendo",
        "AVISO AOS ACIONISTAS — Juros sobre o Capital Próprio (JCP)", **JCP_BMRD,
    ))
    assert reg.status == "revisao_humana"
    assert reg.eventos[0].tipo.confianca == "baixa"


def test_hallucinated_quote_gets_low_confidence():
    bad = JCP_BMRD | {"valor_bruto": ("0.1838420000", "R$ 0,1838420000")}
    reg = run("02_banco_meridional_jcp.pdf", extraction(
        IDENT_BMRD, ("2026-06-02", "São Paulo (SP), 02 de junho de 2026."), "jcp",
        "AVISO AOS ACIONISTAS — Juros sobre o Capital Próprio (JCP)", **bad,
    ))
    bruto = reg.eventos[0].campos["valor_bruto"]
    assert bruto.confianca == "baixa"
    assert any("não foi localizado" in m for m in bruto.motivos)
    assert reg.status == "revisao_humana"


def test_doc03_title_says_dividend_content_is_jcp():
    reg = run("03_siderurgica_paranaense_proventos.pdf", extraction(
        {
            "emissor": ("Companhia Siderúrgica Paranaense S.A.", "A Companhia Siderúrgica Paranaense S.A."),
            "cnpj": ("76.543.210/0001-12", "CNPJ/ME nº 76.543.210/0001-12"),
            "isin": ("BRCSPRACNOR1", "CSPR3 (ISIN BRCSPRACNOR1)"),
            "ticker": ("CSPR3", "CSPR3 (ISIN BRCSPRACNOR1)"),
            "classe_acao": ("ON", "Valor bruto por ação ON"),
        },
        ("2026-06-05", "Curitiba (PR), 05 de junho de 2026."), "jcp",
        "AVISO AOS ACIONISTAS — Distribuição de Dividendos",
        data_aprovacao=("2026-06-05", "em reunião de 05 de junho de 2026"),
        data_com=("2026-06-19", "Data-base (“data com”) 19/06/2026"),
        data_ex=("2026-06-22", "Data “ex” 22/06/2026"),
        data_pagamento=("2026-09-11", "Data de pagamento 11/09/2026"),
        moeda=("BRL", "R$ 0,0921500000"),
        valor_bruto=("0.0921500000", "R$ 0,0921500000"),
        aliquota_ir=("17.5", "à alíquota de 17,5%"),
        valor_liquido=("0.0760237500", "R$ 0,0760237500"),
    ))
    assert reg.status == "revisao_humana"
    assert reg.eventos[0].tipo.valor == "jcp"
    assert reg.eventos[0].tipo.confianca == "media"
    assert paths(reg) == {"eventos[0].tipo"}


def test_doc04_payment_tbd_is_pending_not_review():
    reg = run("04_rede_varejo_jcp_sem_data.pdf", extraction(
        {
            "emissor": ("Rede Varejo Brasil S.A.", "A Rede Varejo Brasil S.A."),
            "cnpj": ("08.777.666/0001-33", "CNPJ/ME nº 08.777.666/0001-33"),
            "isin": ("BRRVBRACNOR9", "RVBR3 (ISIN BRRVBRACNOR9)"),
            "ticker": ("RVBR3", "RVBR3 (ISIN BRRVBRACNOR9)"),
            "classe_acao": ("ON", "Valor bruto por ação ON"),
        },
        ("2026-06-09", "São Paulo (SP), 09 de junho de 2026."), "jcp",
        "AVISO AOS ACIONISTAS — Juros sobre o Capital Próprio",
        data_aprovacao=("2026-06-09", "em reunião realizada em 09 de junho de 2026"),
        data_com=("2026-06-23", "Data-base (“data com”) 23/06/2026"),
        data_ex=("2026-06-24", "Data “ex-JCP” 24/06/2026"),
        data_pagamento=(None, "A definir (vide aviso complementar)"),
        moeda=("BRL", "R$ 0,2050000000"),
        valor_bruto=("0.2050000000", "R$ 0,2050000000"),
        aliquota_ir=("17.5", "IRRF na fonte 17,5%"),
        valor_liquido=("0.1691250000", "R$ 0,1691250000"),
    ))
    assert reg.status == "aprovado_com_ressalvas", reg.revisao_humana
    assert reg.eventos[0].campos["data_pagamento"].status == "ausente_no_documento"
    assert len(reg.pendencias) == 1


def test_doc05_payment_before_record_date():
    reg = run("05_aurora_saneamento_dividendo_datas.pdf", extraction(
        {
            "emissor": ("Aurora Saneamento S.A.", "A Aurora Saneamento S.A."),
            "cnpj": ("22.333.444/0001-77", "CNPJ/ME nº 22.333.444/0001-77"),
            "isin": ("BRAURSACNOR4", "AURS3 (ISIN BRAURSACNOR4)"),
            "ticker": ("AURS3", "AURS3 (ISIN BRAURSACNOR4)"),
            "classe_acao": ("ON", "Valor bruto por ação ON"),
        },
        ("2026-06-01", "Belo Horizonte (MG), 01 de junho de 2026."), "dividendo",
        "AVISO AOS ACIONISTAS — Pagamento de Dividendos Intercalares",
        data_aprovacao=("2026-06-01", "Data de aprovação (RCA) 01/06/2026"),
        data_com=("2026-07-15", "Data-base (“data com”) 15/07/2026"),
        data_ex=("2026-07-16", "Data “ex-dividendos” 16/07/2026"),
        data_pagamento=("2026-07-10", "Data de pagamento 10/07/2026"),
        moeda=("BRL", "R$ 0,3100000000"),
        valor_bruto=("0.3100000000", "R$ 0,3100000000"),
    ))
    assert reg.status == "revisao_humana"
    assert reg.eventos[0].campos["data_pagamento"].confianca == "baixa"
    assert any("pagamento_apos_com" in i.motivo for i in reg.revisao_humana)


SCAN_TEXT = """TELECOM NORTE PARTICIPAÇÕES S.A. Juros sobre o Capital Próprio (JCP)
nos termos do art. 9º da Lei nº 9.249/95, com retenção de IRRF à alíquota de 17,5%"""


def test_doc07_scan_only_unverifiable_fields_go_to_review():
    reg = run("07_telecom_norte_jcp_SCAN.pdf", extraction(
        {
            "emissor": ("Telecom Norte Participações S.A.", "TELECOM NORTE PARTICIPAÇÕES S.A."),
            "cnpj": ("33.222.111/0001-44", "CNPJ/ME nº 33.222.111/0001-44"),
            "isin": ("BRTLNRACNPR2", "TLNR4 (ISIN BRTLNRACNPR2)"),
            "ticker": ("TLNR4", "TLNR4 (ISIN BRTLNRACNPR2)"),
            "classe_acao": ("PN", "Valor bruto por ação PN"),
        },
        ("2026-06-06", "São Paulo (SP), 06 de junho de 2026."), "jcp",
        "Juros sobre o Capital Próprio (JCP)", transcricao=SCAN_TEXT,
        data_aprovacao=("2026-06-06", "em reunião realizada em 06 de junho de 2026"),
        data_com=("2026-06-22", "Data-base (data com) 22/06/2026"),
        data_ex=("2026-06-23", "Data ex-JCP 23/06/2026"),
        data_pagamento=("2026-08-21", "Data de pagamento 21/08/2026"),
        moeda=("BRL", "R$ 0,1124300000"),
        valor_bruto=("0.1124300000", "R$ 0,1124300000"),
        aliquota_ir=("17.5", "IRRF na fonte 17,5%"),
        valor_liquido=("0.0927547500", "R$ 0,0927547500"),
    ))
    assert reg.documento.fonte == "imagem_escaneada"
    campos = reg.eventos[0].campos
    assert campos["valor_bruto"].confianca == "alta"
    assert campos["data_com"].confianca == "alta"
    assert campos["data_pagamento"].confianca == "media"
    assert paths(reg) == {"eventos[0].data_pagamento"}


def test_doc08_issuer_outside_golden_base():
    reg = run("08_construtora_horizonte_bonificacao.pdf", extraction(
        {
            "emissor": ("Construtora Horizonte S.A.", "A Construtora Horizonte S.A."),
            "cnpj": ("09.888.999/0001-21", "CNPJ/ME nº 09.888.999/0001-21"),
            "isin": ("BRCNHZACNOR5", "CNHZ3 (ISIN BRCNHZACNOR5)"),
            "ticker": ("CNHZ3", "CNHZ3 (ISIN BRCNHZACNOR5)"),
            "classe_acao": (None, None),
        },
        ("2026-06-04", "São Paulo (SP), 04 de junho de 2026."), "bonificacao",
        "AVISO AOS ACIONISTAS — Bonificação em Ações",
        data_aprovacao=("2026-06-04", "Assembleia Geral Extraordinária de 04 de junho de 2026"),
        data_com=("2026-06-18", "Data-base (“data com”) 18/06/2026"),
        data_ex=("2026-06-19", "Data “ex-bonificação” 19/06/2026"),
        data_pagamento=("2026-06-26", "Crédito das ações bonificadas 26/06/2026"),
        moeda=("BRL", "R$ 7,820000 por ação"),
        proporcao=("1:20", "1 ação nova para cada 20 ações (5%)"),
        percentual_bonificacao=("5", "1 ação nova para cada 20 ações (5%)"),
        custo_atribuido=("7.820000", "Custo atribuído (base fiscal) R$ 7,820000 por ação"),
    ))
    assert reg.status == "revisao_humana"
    assert reg.validacao_golden["status"] == "nao_encontrado"
    assert reg.identificacao["isin"].confianca == "media"
    assert "× 21/20" in reg.eventos[0].efeito
    assert reg.eventos[0].campos["valor_bruto"].status == "nao_aplicavel"


@pytest.mark.parametrize("valor,trecho,ok", [
    ("ON", "Valor bruto por ação ordinária R$ 0,4275000000", True),
    ("PN", "Valor bruto por ação preferencial R$ 0,1738420000", True),
    ("ON", "Código de negociação PQLT3 (ISIN BRPQLTACNOR8)", True),
    ("PN", "Código de negociação PQLT3 (ISIN BRPQLTACNOR8)", False),
    ("ON", "Data de pagamento 03/07/2026", False),
])
def test_share_class_matches_name_ticker_or_isin(valor, trecho, ok):
    assert _value_matches("classe_acao", valor, trecho)[0] is ok


def test_paraphrased_quote_with_confirmed_value_is_medium_not_low():
    paraphrased = JCP_BMRD | {"aliquota_ir": ("17.5", "IRRF à alíquota de 17,5%")}
    reg = run("02_banco_meridional_jcp.pdf", extraction(
        IDENT_BMRD, ("2026-06-02", "São Paulo (SP), 02 de junho de 2026."), "jcp",
        "AVISO AOS ACIONISTAS — Juros sobre o Capital Próprio (JCP)", **paraphrased,
    ))
    aliq = reg.eventos[0].campos["aliquota_ir"]
    assert aliq.confianca == "media"
    assert any("citação não é literal" in m for m in aliq.motivos)
    assert not any("possível alucinação" in m for m in aliq.motivos)
    assert paths(reg) == {"eventos[0].aliquota_ir"}
