import json
from collections import Counter
from pathlib import Path

from case_btg.config import OUTPUT_DIR
from case_btg.schema import Registro

STATUS_LABEL = {
    "aprovado": "✅ aprovado",
    "aprovado_com_ressalvas": "🟡 aprovado com ressalvas",
    "revisao_humana": "🔴 revisão humana",
}


def write_record(reg: Registro) -> Path:
    path = OUTPUT_DIR / "records" / f"{Path(reg.documento.arquivo).stem}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(reg.model_dump(mode="json"), ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def _event_label(reg: Registro) -> str:
    ticker = reg.identificacao["ticker"].valor or "?"
    tipos = ", ".join(ev.tipo.valor for ev in reg.eventos) or "—"
    return f"{tipos} · {ticker}"


def write_report(records: list[Registro], failures: dict[str, str]) -> Path:
    counts = Counter(r.status for r in records)
    lines = [
        "# Relatório de exceções",
        "",
        f"{len(records) + len(failures)} documentos: {counts['aprovado']} aprovados, "
        f"{counts['aprovado_com_ressalvas']} com ressalvas, {counts['revisao_humana']} em revisão humana, "
        f"{len(failures)} falhas de processamento.",
        "",
        "| Documento | Evento | Status | Revisão | Pendências |",
        "|---|---|---|---|---|",
    ]
    for r in records:
        lines.append(f"| {r.documento.arquivo} | {_event_label(r)} | {STATUS_LABEL[r.status]} "
                     f"| {len(r.revisao_humana)} | {len(r.pendencias)} |")
    for name in failures:
        lines.append(f"| {name} | — | ⚫ falha de processamento | — | — |")

    review = [r for r in records if r.revisao_humana]
    if review:
        lines += ["", "## Revisão humana", ""]
        for r in review:
            lines += [f"### {r.documento.arquivo} — {_event_label(r)}", "", r.resumo_operador, ""]
            for item in r.revisao_humana:
                lines.append(f"- **{item.campo}** ({item.severidade}): {item.motivo}  \n  → {item.acao_sugerida}")
            lines.append("")

    pending = [r for r in records if r.pendencias]
    if pending:
        lines += ["## Pendências (registro aprovado, aguardando informação)", ""]
        for r in pending:
            lines += [f"- **{r.documento.arquivo}**: {p}" for p in r.pendencias]
        lines.append("")

    if failures:
        lines += ["## Falhas de processamento", ""]
        lines += [f"- **{name}**: {err}" for name, err in failures.items()]
        lines.append("")

    path = OUTPUT_DIR / "exceptions_report.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")
    return path
