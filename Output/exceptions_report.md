# Relatório de exceções

8 documentos: 2 aprovados, 1 com ressalvas, 5 em revisão humana, 0 falhas de processamento.

| Documento | Evento | Status | Revisão | Pendências |
|---|---|---|---|---|
| 01_energetica_vale_tiete_dividendo.pdf | dividendo · TIET3 | ✅ aprovado | 0 | 0 |
| 02_banco_meridional_jcp.pdf | jcp · BMRD4 | 🔴 revisão humana | 1 | 0 |
| 03_siderurgica_paranaense_proventos.pdf | jcp · CSPR3 | 🔴 revisão humana | 1 | 0 |
| 04_rede_varejo_jcp_sem_data.pdf | jcp · RVBR3 | 🟡 aprovado com ressalvas | 0 | 1 |
| 05_aurora_saneamento_dividendo_datas.pdf | dividendo · AURS3 | 🔴 revisão humana | 2 | 0 |
| 06_petroquimica_litoral_grupamento.pdf | grupamento · PQLT3 | ✅ aprovado | 0 | 0 |
| 07_telecom_norte_jcp_SCAN.pdf | jcp · TLNR4 | 🔴 revisão humana | 1 | 0 |
| 08_construtora_horizonte_bonificacao.pdf | bonificacao · CNHZ3 | 🔴 revisão humana | 1 | 0 |

## Revisão humana

### 02_banco_meridional_jcp.pdf — jcp · BMRD4

JCP BMRD4. Crédito em dinheiro: R$ 0,1738420000 bruto por ação, R$ 0,1434196500 líquido após IRRF de 17,5%. Data com 16/06/2026, ex 17/06/2026, pagamento/crédito 14/08/2026. Requer revisão humana: 1 item(ns).

- **eventos[0].aliquota_ir** (revisao): valor confirmado por valores.bruto_liquido_ir, mas a citação não é literal (trecho não existe no PDF): evidência não auditável  
  → Conferir no documento original antes de processar

### 03_siderurgica_paranaense_proventos.pdf — jcp · CSPR3

JCP CSPR3. Crédito em dinheiro: R$ 0,0921500000 bruto por ação, R$ 0,0760237500 líquido após IRRF de 17,5%. Data com 19/06/2026, ex 22/06/2026, pagamento/crédito 11/09/2026. Requer revisão humana: 1 item(ns).

- **eventos[0].tipo** (revisao): classificacao.rotulo_vs_conteudo: título indica 'dividendo', conteúdo indica 'jcp' ('AVISO AOS ACIONISTAS — Distribuição de Dividendos')  
  → Confirmar a natureza do evento: o tipo define o tratamento tributário

### 05_aurora_saneamento_dividendo_datas.pdf — dividendo · AURS3

Dividendo AURS3. Crédito em dinheiro: R$ 0,3100000000 bruto por ação; líquido depende do beneficiário (sem alíquota uniforme por ação). Data com 15/07/2026, ex 16/07/2026, pagamento/crédito 10/07/2026. Requer revisão humana: 2 item(ns).

- **eventos[0].data_pagamento, eventos[0].data_com** (bloqueante): datas.pagamento_apos_com: pagamento 2026-07-10 > com 2026-07-15  
  → Confirmar as datas com a área de RI do emissor; não processar até corrigir
- **eventos[0].data_pagamento, eventos[0].data_ex** (revisao): datas.pagamento_apos_ex: pagamento 2026-07-10 >= ex 2026-07-16  
  → Confirmar as datas com a área de RI do emissor; não processar até corrigir

### 07_telecom_norte_jcp_SCAN.pdf — jcp · TLNR4

JCP TLNR4. Crédito em dinheiro: R$ 0,1124300000 bruto por ação, R$ 0,0927547500 líquido após IRRF de 17,5%. Data com 22/06/2026, ex 23/06/2026, pagamento/crédito 21/08/2026. Requer revisão humana: 1 item(ns).

- **eventos[0].data_pagamento** (revisao): lido de imagem (PDF sem camada de texto): trecho não verificável  
  → Conferir o valor na imagem do documento original

### 08_construtora_horizonte_bonificacao.pdf — bonificacao · CNHZ3

Bonificação CNHZ3. Crédito de ações: 1 nova(s) para cada 20 detidas (quantidade × 21/20); custo atribuído R$ 7,820000 por ação nova. Data com 18/06/2026, ex 19/06/2026, pagamento/crédito 26/06/2026. Requer revisão humana: 1 item(ns).

- **identificacao.isin, identificacao.ticker, identificacao.cnpj, identificacao.emissor** (revisao): golden.identificacao: emissor/ativo não consta na base de referência  
  → Confirmar identificação do ativo em fonte oficial (B3/CVM) e atualizar a base de referência

## Pendências (registro aprovado, aguardando informação)

- **04_rede_varejo_jcp_sem_data.pdf**: eventos[0].data_pagamento: não informado no aviso ("Data de pagamento       A definir (vide aviso complementar)")
