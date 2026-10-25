"""Dois contratos: o que o LLM devolve (Extracao*) e o registro final auditável (Registro*)."""

from typing import Literal

from pydantic import BaseModel, Field

TipoEvento = Literal["dividendo", "jcp", "bonificacao", "grupamento", "desdobramento", "outro"]
Confianca = Literal["alta", "media", "baixa"]
Severidade = Literal["bloqueante", "revisao", "info"]
StatusDocumento = Literal["aprovado", "aprovado_com_ressalvas", "revisao_humana"]


# ---------------------------------------------------------------- saída do LLM


class CampoExtraido(BaseModel):
    valor: str | None = Field(
        description="Valor normalizado (datas AAAA-MM-DD; números com ponto decimal, sem milhar, "
        "sem R$ ou %; proporção 'A:B'). null se o documento não informa."
    )
    trecho: str | None = Field(
        description="Cópia LITERAL do trecho do documento que contém o valor (ou que declara a ausência, "
        "ex.: 'A definir'). Não parafrasear."
    )
    pagina: int | None = None


class IdentificacaoExtraida(BaseModel):
    emissor: CampoExtraido
    cnpj: CampoExtraido
    isin: CampoExtraido
    ticker: CampoExtraido
    classe_acao: CampoExtraido = Field(description="ON, PN, PNA, PNB ou UNIT")


class EventoExtraido(BaseModel):
    tipo_evento: TipoEvento = Field(description="Classificação pela SUBSTÂNCIA do evento, não pelo título.")
    justificativa_tipo: str = Field(description="Quais elementos do texto determinam o tipo.")
    rotulo_documento: CampoExtraido = Field(description="Como o título do aviso nomeia o evento, literal.")
    data_aprovacao: CampoExtraido
    data_com: CampoExtraido = Field(description="Data com / data-base (último dia com direito).")
    data_ex: CampoExtraido = Field(
        description="Primeiro dia 'ex'. Em grupamento/desdobramento: início da negociação ajustada."
    )
    data_pagamento: CampoExtraido = Field(
        description="Pagamento (proventos em dinheiro) ou crédito das ações (bonificação)."
    )
    moeda: CampoExtraido = Field(description="Código ISO, ex.: BRL.")
    valor_bruto: CampoExtraido = Field(description="Valor bruto por ação.")
    aliquota_ir: CampoExtraido = Field(
        description="Alíquota de IR em %, só se aplicada uniformemente sobre o valor por ação (ex.: 17.5)."
    )
    valor_liquido: CampoExtraido = Field(description="Valor líquido por ação, se informado.")
    proporcao: CampoExtraido = Field(
        description="Grupamento/desdobramento: 'antes:depois' (10 viram 1 = '10:1'). "
        "Bonificação: 'novas:base' (1 nova para cada 20 = '1:20')."
    )
    percentual_bonificacao: CampoExtraido = Field(description="Percentual da bonificação, se informado (ex.: 5).")
    custo_atribuido: CampoExtraido = Field(description="Custo unitário atribuído às ações bonificadas (R$).")
    observacoes: list[str] = Field(default_factory=list, description="Condições relevantes não estruturadas.")


class Extracao(BaseModel):
    identificacao: IdentificacaoExtraida
    data_documento: CampoExtraido = Field(description="Data de emissão do aviso (local e data no rodapé).")
    eventos: list[EventoExtraido]
    transcricao: str | None = Field(
        default=None, description="Só para documentos em imagem: transcrição integral do texto."
    )


# ---------------------------------------------------------------- registro final


class Evidencia(BaseModel):
    trecho: str
    pagina: int | None
    linha: int | None = None
    verificada: bool | None = Field(description="Trecho localizado no texto do PDF. null = PDF sem camada de texto.")


class Campo(BaseModel):
    valor: str | None
    unidade: str | None = None
    status: Literal["extraido", "ausente_no_documento", "nao_encontrado", "nao_aplicavel"]
    confianca: Confianca | None
    motivos: list[str] = Field(default_factory=list)
    evidencia: Evidencia | None = None


class Verificacao(BaseModel):
    regra: str
    resultado: Literal["ok", "falha", "conflito", "nao_validado", "pendente"]
    severidade: Severidade
    detalhe: str
    campos: list[str] = Field(default_factory=list)


class ItemRevisao(BaseModel):
    campo: str
    severidade: Severidade
    motivo: str
    acao_sugerida: str


class Evento(BaseModel):
    tipo: Campo
    rotulo_documento: str | None
    efeito: str | None = Field(description="O que o evento faz com a posição do cliente, em linguagem de operação.")
    campos: dict[str, Campo]
    observacoes: list[str]


class DocumentoInfo(BaseModel):
    arquivo: str
    sha256: str
    paginas: int
    fonte: Literal["texto_nativo", "imagem_escaneada"]


class Auditoria(BaseModel):
    modelo: str
    prompt_versao: str
    processado_em: str
    resposta_llm_cache: str | None = None


class Registro(BaseModel):
    documento: DocumentoInfo
    status: StatusDocumento
    resumo_operador: str
    identificacao: dict[str, Campo]
    data_documento: Campo
    validacao_golden: dict
    eventos: list[Evento]
    revisao_humana: list[ItemRevisao]
    pendencias: list[str]
    verificacoes: list[Verificacao]
    auditoria: Auditoria
