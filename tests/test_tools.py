from fractions import Fraction

import pytest

from case_btg.config import INPUT_DIR
from case_btg.ingest import load_document
from case_btg.tools.amounts import check_gross_net, check_ratio_percent, parse_brl, parse_ratio
from case_btg.tools.dates import next_business_day, validate_dates
from case_btg.tools.evidence import count_mentions, locate_quote
from case_btg.tools.golden import lookup_golden_record
from datetime import date


def _failed(checks):
    return {c["regra"] for c in checks if c["resultado"] == "falha"}


# --- ingest ---

def test_native_pdf_has_text():
    doc = load_document(INPUT_DIR / "01_energetica_vale_tiete_dividendo.pdf")
    assert doc.source == "texto_nativo"
    assert "TIET3" in doc.text


def test_scanned_pdf_is_rendered_as_image():
    doc = load_document(INPUT_DIR / "07_telecom_norte_jcp_SCAN.pdf")
    assert doc.source == "imagem_escaneada"
    assert doc.pages[0].image_png.startswith(b"\x89PNG")


# --- golden ---

def test_golden_all_keys_match():
    r = lookup_golden_record(isin="BRBMRDACNPR7", ticker="BMRD4", cnpj="60.111.222/0001-55",
                             emissor="BANCO MERIDIONAL DO BRASIL S.A.")
    assert r["status"] == "ok"
    assert r["registro"]["classe"] == "PN" == r["classe_pelo_ticker"]


def test_golden_issuer_not_in_base():
    r = lookup_golden_record(isin="BRCNHZACNOR5", ticker="CNHZ3", cnpj="09.888.999/0001-21")
    assert r["status"] == "nao_encontrado"


def test_golden_conflict_between_keys():
    r = lookup_golden_record(isin="BRTIETACNOR3", ticker="BMRD4")
    assert r["status"] == "conflito"


def test_fictitious_isin_fails_check_digit():
    assert lookup_golden_record(isin="BRTIETACNOR3")["isin_digito_verificador_ok"] is False


# --- dates ---

def test_next_business_day_skips_weekend_and_corpus_christi():
    assert next_business_day(date(2026, 6, 12)) == date(2026, 6, 15)
    assert next_business_day(date(2026, 6, 3)) == date(2026, 6, 5)


def test_dates_doc01_coherent():
    checks = validate_dates("dividendo", "2026-05-28", "2026-06-12", "2026-06-15", "2026-07-03", "2026-05-28")
    assert _failed(checks) == set()


def test_dates_doc05_payment_before_record_date():
    checks = validate_dates("dividendo", "2026-06-01", "2026-07-15", "2026-07-16", "2026-07-10")
    assert {"datas.pagamento_apos_com", "datas.pagamento_apos_ex"} <= _failed(checks)


def test_dates_doc04_missing_payment_is_pending_not_failure():
    checks = validate_dates("jcp", "2026-06-09", "2026-06-23", "2026-06-24", None)
    assert _failed(checks) == set()
    assert any(c["regra"] == "datas.pagamento_ausente" for c in checks)


# --- amounts ---

def test_parse_brl_keeps_scale():
    assert str(parse_brl("R$ 0,4275000000")) == "0.4275000000"
    assert str(parse_brl("R$ 1.234,56")) == "1234.56"


@pytest.mark.parametrize("bruto,liquido", [
    ("0,1738420000", "0,1434196500"),  # doc 02
    ("0,0921500000", "0,0760237500"),  # doc 03
    ("0,2050000000", "0,1691250000"),  # doc 04
    ("0,1124300000", "0,0927547500"),  # doc 07 (scan)
])
def test_gross_net_batch(bruto, liquido):
    assert check_gross_net(bruto, "17,5%", liquido)["resultado"] == "ok"


def test_gross_net_detects_wrong_value():
    assert check_gross_net("0,1738420000", "17,5%", "0,1434000000")["resultado"] == "falha"


def test_ratios():
    assert parse_ratio("10:1 (dez para uma)") == Fraction(10, 1)
    assert check_ratio_percent(1, 20, "5%")["resultado"] == "ok"


# --- evidence ---

def test_quote_found_across_line_break():
    doc = load_document(INPUT_DIR / "03_siderurgica_paranaense_proventos.pdf")
    r = locate_quote(doc.text, "a título de remuneração do capital próprio")
    assert r["encontrado"] and r["linha"]


def test_hallucinated_quote_not_found():
    doc = load_document(INPUT_DIR / "03_siderurgica_paranaense_proventos.pdf")
    assert not locate_quote(doc.text, "Juros sobre o Capital Próprio (JCP)")["encontrado"]


def test_value_mentioned_in_body_and_table():
    doc = load_document(INPUT_DIR / "01_energetica_vale_tiete_dividendo.pdf")
    assert count_mentions(doc.text, "2026-06-12", "data") == 2
    assert count_mentions(doc.text, "0.4275", "valor") == 1
