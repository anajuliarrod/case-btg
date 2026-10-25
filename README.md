# Case AI Dev: extração auditável de avisos de eventos corporativos

Agente code-first que lê avisos aos acionistas (PDF nativo ou escaneado), extrai os dados do evento (dividendo, JCP, bonificação, grupamento), valida contra a base de referência e contra regras de coerência, atribui confiança por campo com justificativa e decide o que vai para revisão humana.

Saída: um JSON por documento em `Output/records/` e o relatório `Output/exceptions_report.md`.

## Como rodar

Requer Python 3.12+ e [uv](https://docs.astral.sh/uv/).

```bash
uv sync
cp .env.example .env          # preencha GEMINI_API_KEY (gratuita em aistudio.google.com/apikey)
uv run case-btg               # processa Input/*.pdf
uv run pytest                 # 35 testes, sem LLM
```

| Comando | O que faz |
|---|---|
| `uv run case-btg` | Usa a resposta do LLM em cache quando existe; senão chama o LLM |
| `uv run case-btg --mode replay` | **Só cache, sem API key.** Reproduz exatamente a saída entregue |
| `uv run case-btg --mode refresh` | Ignora o cache e chama o LLM de novo |
| `uv run case-btg --only 07` | Processa só os arquivos cujo nome contém `07` |

Provider e modelo por variável de ambiente:

| Variável | Padrão | Observação |
|---|---|---|
| `LLM_PROVIDER` | `gemini` | `gemini` (API key) ou `claude` (Claude Agent SDK, usa o login local do Claude Code) |
| `GEMINI_MODEL` | `gemini-flash-latest` | |
| `CLAUDE_MODEL` | `sonnet` | |

**Outro provider:** crie `src/case_btg/llm/<nome>.py` com `run(doc, system, fns, call_tool) -> (Extracao, nome_do_modelo)` e registre o módulo em `BACKENDS` (`agent.py`). Gate, tools, cache e relatório não mudam. Os dois backends existentes servem de modelo: `fns` são funções Python comuns (nome, docstring e parâmetros viram a declaração da tool) e `call_tool(nome, args)` executa a tool e registra a chamada na trilha de auditoria.

As respostas dos LLMs ficam em `cache/llm/`, versionadas no repositório, com o arquivo, o hash do PDF, a versão do prompt, o modelo e **todas as chamadas de ferramenta com argumentos e resultados**.

## Arquitetura

```
PDF ─► ingest ─► agente (LLM + tools) ─► gate determinístico ─► JSON + relatório
       texto ou      extrai e cita          refaz as validações,
       imagem        trechos; chama         calcula a confiança,
                     as tools               decide o roteamento
```

| Módulo | Papel | LLM? |
|---|---|---|
| `ingest.py` | Texto por página via pdfplumber. Página sem camada de texto é renderizada (pymupdf) e marcada como `imagem_escaneada` | não |
| `agent.py` + `llm/` | Monta o prompt, expõe as tools, registra as chamadas, faz o cache. Backends Gemini e Claude atrás da mesma interface | **sim** |
| `tools/` | `lookup_golden_record`, `validate_dates`, `check_gross_net`, `verificar_trecho`: as **mesmas funções** que o LLM chama e que o gate reexecuta | não |
| `gate.py` | Confiança por campo, verificações e roteamento | não |
| `report.py` | JSONs e relatório de exceções | não |

**Princípio central: o LLM propõe e o código decide.** O LLM extrai os valores e cita o trecho literal de onde veio cada um. Ele usa as tools para se autoverificar antes de entregar, o que corrige erros de leitura. Mas nada do que ele afirma sobre a própria confiança entra no registro: o gate refaz todas as validações sobre a extração final, mesmo que o LLM tenha pulado alguma tool.

## Como ler o JSON

```jsonc
{
  "status": "revisao_humana",            // aprovado | aprovado_com_ressalvas | revisao_humana
  "resumo_operador": "JCP CSPR3. Crédito em dinheiro: R$ 0,0921500000 bruto por ação, ...",
  "identificacao": { "isin": { /* campo */ }, "ticker": {}, "cnpj": {}, "emissor": {}, "classe_acao": {} },
  "validacao_golden": { "status": "ok", "registro": { /* linha da base */ } },
  "eventos": [{
    "tipo": { "valor": "jcp", "confianca": "media", "motivos": ["...título indica 'dividendo'..."] },
    "efeito": "Crédito em dinheiro: ...",   // o que acontece com a posição do cliente
    "campos": {
      "valor_bruto": {
        "valor": "0.0921500000", "unidade": "BRL/acao", "status": "extraido", "confianca": "alta",
        "motivos": ["trecho localizado no PDF (pág. 1, linha 28)", "confirmado por valores.bruto_liquido_ir"],
        "evidencia": { "trecho": "Valor bruto por ação ON R$ 0,0921500000", "pagina": 1, "linha": 28, "verificada": true }
      }
    }
  }],
  "revisao_humana": [{ "campo": "eventos[0].tipo", "motivo": "...", "acao_sugerida": "..." }],
  "pendencias": [],                      // dado que o próprio aviso diz não ter (ex.: "A definir")
  "verificacoes": [ /* todas as regras executadas, inclusive as que passaram */ ],
  "auditoria": { "modelo": "...", "prompt_versao": "v1", "resposta_llm_cache": "cache/llm/..." }
}
```

Cada campo responde às perguntas do operador: **qual o valor** (`valor`, `unidade`), **de onde veio** (`evidencia.trecho`, página, linha, e se o trecho foi achado no PDF), **quanto confiar** (`confianca`) e **por quê** (`motivos`). O `status` do campo distingue quatro situações que uma extração ingênua confunde: `extraido`; `ausente_no_documento`, quando o aviso declara que não tem o dado, como "A definir"; `nao_encontrado`, quando o dado deveria existir e não foi achado; e `nao_aplicavel`, como valor em R$ num grupamento.

Valores monetários são strings decimais com a precisão do documento (`"0.4275000000"`), nunca float. O `resumo_operador` é gerado por template a partir dos campos validados, não pelo LLM.

## Confiança por campo

Níveis discretos com motivos explícitos, em vez de um score numérico. Um `0.87` não diz ao operador o que conferir; "trecho não localizado no PDF" diz.

| Situação | Confiança |
|---|---|
| Trecho citado localizado no texto do PDF e o valor corresponde ao trecho | **alta** |
| Lido de imagem (scan), sem verificação independente | **media** |
| Lido de imagem, mas confirmado por verificação cruzada: golden record, bruto × (1 − IR) = líquido, data ex = próximo pregão após a data com, proporção = percentual, moeda × país do ISIN | sobe para **alta** |
| Trecho citado não existe no PDF, mas o valor foi confirmado por verificação cruzada | **media** ("evidência não auditável") |
| Emissor fora da base de referência (não validável) | cai para **media** |
| Título e conteúdo divergem quanto ao tipo | cai para **media** |
| Trecho não encontrado no PDF (possível alucinação), valor diferente do trecho, sem trecho, ou alguma validação falhou | **baixa** |

O tipo do evento recebe uma segunda opinião determinística: regras de palavra-chave sobre o texto ("capital próprio" e "Lei 9.249" → JCP; "grupamento" e "inplit" → grupamento; e assim por diante). LLM e regras discordando → **baixa**.

## Roteamento

| Status | Quando |
|---|---|
| `revisao_humana` | Algum campo com confiança baixa; campo **crítico** com confiança média; verificação bloqueante ou de revisão falhou; emissor fora da base; conflito de classificação |
| `aprovado_com_ressalvas` | Tudo validado, mas falta um dado crítico que o próprio aviso declara indisponível. Vira pendência, não erro |
| `aprovado` | Nenhum dos casos acima |

Campos críticos por tipo: identificação (emissor, ISIN, ticker) mais data com, data ex e:
- **dividendo**: valor bruto, moeda, pagamento;
- **JCP**: o mesmo do dividendo, mais alíquota e líquido;
- **bonificação**: proporção e custo atribuído;
- **grupamento/desdobramento**: proporção.

Cada item de revisão traz o campo, o motivo e uma ação sugerida.

## Verificações

| Regra | Severidade | Fundamento |
|---|---|---|
| `golden.identificacao` | bloqueante | ISIN, ticker, CNPJ e emissor precisam apontar para o mesmo registro da base |
| `golden.classe` | revisão | ON/PN do documento = classe da base |
| `datas.com_antes_ex`, `datas.pagamento_apos_com`, `datas.aprovacao_ate_com` | bloqueante | Ordem lógica do evento |
| `datas.ex_proximo_dia_util` | revisão | Data ex = próximo pregão B3 após a data com |
| `datas.pagamento_apos_ex`, `datas.com_dia_util`, `datas.aprovacao_ate_documento` | revisão | |
| `valores.bruto_liquido_ir` | bloqueante | Líquido = bruto × (1 − alíquota), com tolerância de meia unidade na última casa |
| `valores.proporcao_vs_percentual` | bloqueante | Bonificação 1:20 = 5% |
| `classificacao.palavras_chave` | bloqueante | LLM e regras determinísticas concordam no tipo |
| `classificacao.rotulo_vs_conteudo` | revisão | Título do aviso × substância (doc 03) |
| `classificacao.tributacao_coerente` | revisão | JCP sem IRRF, ou dividendo com IR uniforme por ação (padrão de JCP) |
| `classificacao.proporcao_vs_tipo` | bloqueante | Grupamento reduz a quantidade, desdobramento aumenta |
| `isin.digito_verificador` | info | Ver premissas |

## Resultado no lote

Execução de referência com `gemini-3.8-flash` (cache em `cache/llm/*.gemini.json`; `Output/` corresponde a ela):

| Doc | Evento | Status | Por quê |
|---|---|---|---|
| 01 | Dividendo TIET3 | ✅ aprovado | Tudo localizado e coerente. Líquido `null`: IR de 10% só acima de R$ 50 mil/mês, sem valor uniforme por ação |
| 02 | JCP BMRD4 | 🔴 revisão | Valores corretos (0,173842 × (1 − 17,5%) = 0,14341965 ✓), mas o modelo **resumiu a citação** da alíquota ("IRRF à alíquota de 17,5%" em vez do texto literal). Valor confirmado, evidência não auditável → média → revisão |
| 03 | JCP CSPR3 | 🔴 revisão | **Título diz "Dividendos", conteúdo é JCP** (remuneração do capital próprio, TJLP, IRRF). Classificado como JCP; 1 item: confirmar a natureza |
| 04 | JCP RVBR3 | 🟡 ressalvas | Pagamento "A definir (vide aviso complementar)": pendência, não erro |
| 05 | Dividendo AURS3 | 🔴 revisão | **Pagamento (10/07) antes da data com (15/07) e da data ex (16/07)**. Nenhuma data é "corrigida" |
| 06 | Grupamento PQLT3 | ✅ aprovado | 10:1, quantidade × 1/10, sem crédito em dinheiro. Classe ON deduzida do ticker e confirmada na base |
| 07 | JCP TLNR4 (scan) | 🔴 revisão | Lido de imagem. Valores, datas com/ex e identificação sobem para alta via verificação cruzada; **só a data de pagamento** não tem como ser confirmada |
| 08 | Bonificação CNHZ3 | 🔴 revisão | **Emissor fora da base de referência**. Extração completa: 1:20 (= 5% ✓), custo atribuído R$ 7,82 |

**Comparação com Claude** (`claude-sonnet-5-5`, cache em `cache/llm/*.claude.json`; reproduza com `LLM_PROVIDER=claude uv run case-btg --mode replay`): mesmo roteamento em 7 de 8 documentos. A diferença é o doc 02, que o Claude aprova porque citou a alíquota literalmente. Os dois modelos leram o mesmo valor; só um produziu evidência auditável, e o gate trata as duas situações de forma diferente sem depender de qual modelo rodou.

## Premissas

- **ISINs fictícios.** Todos os ISINs do lote falham no dígito verificador ISO 6166, e o enunciado avisa que são fictícios. A regra roda e é registrada, mas com severidade `info`: não bloqueia. A fonte de verdade da identificação é o golden record.
- **Tributação vigente em 2026, conforme os próprios avisos.** JCP com IRRF de 17,5%; dividendos com retenção de 10% só acima de R$ 50 mil/mês por beneficiário. No segundo caso não existe líquido por ação: `aliquota_ir` e `valor_liquido` ficam `null`, e a regra vai em `observacoes`. O líquido é calculado pelo downstream, beneficiário a beneficiário.
- **Calendário B3 de 2026** fixo no código (feriados nacionais + 24/12 e 31/12). O lote inteiro cai em 2026.
- **Classificação pela substância.** Se título e conteúdo divergem, vale o conteúdo, e o documento vai para revisão mesmo assim, porque errar o tipo muda o tratamento tributário.
- **"A definir" não é erro de extração.** O aviso está correto e completo para o que se propõe. O registro é aprovado com pendência, e o operador sabe que precisa aguardar o aviso complementar.
- **Data incoerente não é corrigida.** No doc 05 o pagamento vem antes da data com; não há como saber qual data está errada. As duas recebem confiança baixa e o documento vai para revisão.
- **Grupamento:** "data com" = data-base do grupamento; "data ex" = início da negociação grupada.

## O que decidi não fazer, e por quê

- **Sem LangChain ou framework de agentes.** O loop de tool calling tem cerca de 30 linhas por provider. Um framework acrescentaria abstração sem resolver nenhum problema deste case, e esconderia o fluxo que precisa ser defendido e depurado ao vivo.
- **Sem OCR clássico (Tesseract).** O scan vai como imagem para o modelo com visão. Isso simplifica a instalação, e a verificação cruzada (bruto/IR/líquido, golden record, calendário) compensa a falta de uma camada de texto para conferir citações. O custo é que, no scan, a citação não pode ser verificada no PDF: o campo fica com confiança média até que outra regra o confirme.
- **Sem confiança declarada pelo LLM e sem score numérico.** A autoavaliação do modelo é mal calibrada, e um número não diz ao operador o que conferir.
- **Sem auto-correção.** Quando uma validação falha, o sistema não "conserta" o valor. Num processo financeiro, um dado errado silencioso é pior que uma pendência visível.
- **Uma só rodada de tools no Gemini.** O free tier permite 20 requisições/dia por modelo; com uma rodada (chamadas em paralelo) mais a entrega, cada documento custa 2 requisições. Perde-se a reverificação após uma correção, mas o gate refaz todas as validações de qualquer forma.
- **Sem segunda leitura independente (self-consistency).** Ler duas vezes e comparar aumentaria a confiança no scan, mas dobraria o custo, e o mesmo modelo lendo a mesma imagem produz leituras correlacionadas. Preferi a verificação cruzada determinística.
- **Calendário e regras tributárias só de 2026.** Generalizar exigiria uma fonte de feriados B3 e de alíquotas por vigência. Fica como extensão natural.
- **Sem banco de dados nem vínculo entre avisos.** O aviso complementar do doc 04, quando chegar, seria um novo documento sem ligação automática com o primeiro.

## Testes

`uv run pytest`: 35 testes, nenhum chama LLM.

- `tests/test_tools.py`: as tools contra os PDFs reais (datas, golden record, bruto/líquido, localização de trechos).
- `tests/test_gate.py`: o gate com extrações escritas à mão, tanto corretas quanto **com erros típicos de LLM**: trecho inventado, JCP classificado como dividendo. Garante que o roteamento esperado para cada documento do lote não depende do modelo.
