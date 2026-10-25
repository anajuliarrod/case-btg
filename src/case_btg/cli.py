import argparse

from dotenv import load_dotenv

from case_btg.agent import extract
from case_btg.config import INPUT_DIR, ROOT
from case_btg.gate import evaluate
from case_btg.ingest import load_document
from case_btg.llm.gemini import QuotaExhausted
from case_btg.report import write_record, write_report


def main() -> None:
    parser = argparse.ArgumentParser(description="Extrai e valida avisos de eventos corporativos.")
    parser.add_argument("--mode", choices=["auto", "replay", "refresh"], default="auto",
                        help="auto: usa cache se houver; replay: só cache, sem API; refresh: sempre chama o LLM")
    parser.add_argument("--only", help="processa só arquivos cujo nome contém este texto")
    args = parser.parse_args()
    load_dotenv(ROOT / ".env")

    pdfs = sorted(p for p in INPUT_DIR.glob("*.pdf") if not args.only or args.only in p.name)
    records, failures = [], {}
    for i, pdf in enumerate(pdfs):
        try:
            doc = load_document(pdf)
            extracao, auditoria = extract(doc, args.mode)
            reg = evaluate(doc, extracao, auditoria)
        except QuotaExhausted as e:
            for rest in pdfs[i:]:
                failures[rest.name] = f"não processado: {e}"
            print(f"✗ {e}; {len(pdfs) - i} documento(s) não processado(s). Rode de novo após a renovação.")
            break
        except Exception as e:  # um documento com falha não derruba o lote; vai para o relatório
            failures[pdf.name] = f"{type(e).__name__}: {e}"
            print(f"✗ {pdf.name}: {failures[pdf.name]}")
            continue
        write_record(reg)
        records.append(reg)
        print(f"{'✓' if reg.status == 'aprovado' else '!'} {pdf.name}: {reg.status}", flush=True)

    print(f"\nRelatório: {write_report(records, failures).relative_to(ROOT)}")


if __name__ == "__main__":
    main()
