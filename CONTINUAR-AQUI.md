# Sugestão de Itens pro Vendedor — CONTINUAR AQUI

> Handoff pra sessão nova continuar do zero, sem re-perguntar o que já ficou decidido.
> Leia tudo antes de tocar em código.

---

## 1. Objetivo (o que o Renato quer)

Um módulo que **cada vendedor da Napel abre digitando filial + número da cotação**, e o
sistema devolve **até 10 sugestões de itens "matadoras"** pro vendedor oferecer ao cliente
naquele momento — depois que o cliente já disse "não preciso de mais nada".

Regra de negócio pra a sugestão ser matadora:

1. Só sugere produtos que **esse cliente já compra na Napel** (histórico dele, não
   catálogo geral).
2. Cada sugestão tem **preço promocional menor que a última vez que o cliente pagou** —
   isso é o gancho de venda ("você pagou X, agora faço por Y").
3. O mix é **aleatório dentro dos aprovados** — pra não oferecer sempre os mesmos itens
   se o vendedor rodar de novo.
4. Cada item vem com **quantidade mínima obrigatória** pra o total do item passar de
   R$ 100 — protege o ticket médio (evita cliente aceitar 1 parafuso de R$ 1,39).

O vendedor gera um layout PDF simples e passa pro cliente (WhatsApp ou impresso).

**Objetivo de negócio:** aumentar ticket médio das vendas — não é reativação de cliente
frio.

## 2. Já existe algo parecido no SIGE que serviu de referência

A tela CTV do SIGE (`Cod_tipo_mv=505`, "Cotação de Venda") tem um menu chamado
**"Sugestão 10 produtos" (`Ctrl+3`)** que gera um PDF com 10 itens do histórico do
cliente, cada um com "Valor Última Compra" e "Valor Promocional". Layout de referência
mora em `C:\Users\Renato\Downloads\232524.pdf` (cliente J.M.BACON, cotação CTV.1 232524
filial 100).

**Diferenças do módulo novo pra esse relatório nativo:**

| | Sugestão nativa SIGE (Ctrl+3) | Módulo novo (este projeto) |
|---|---|---|
| Onde roda | Dentro do SIGE Delphi, tela CTV | Web app standalone (padrão Napel/Clavis) |
| Fórmula do preço promocional | Fechada dentro do binário Delphi (não achei em SQL/procedure/view do SATLBASE — a lógica exata é do BE Resolution) | Definida pelo Renato: `custo × 1,30` com custo vindo de `tbProdutoPcp.Valor_custo_unt` da filial |
| Quantidade mínima | Não tem | Calculada pra total do item ≥ R$ 100 |
| Filtro matador (promo < últ. pago) | Não garante (vi cotação onde promocional saía maior que último pago no PDF do SIGE) | Obrigatório — item descarta se falhar |
| Aleatoriedade | Sim, sorteia diferente a cada geração | Sim, `NEWID()` no SQL / shuffle no JS |
| Layout | Impressora do SIGE, texto simples | Padrão Napel: navy/sky, Archivo/Inter, zero emoji, tabela limpa |

## 3. Fórmula final validada (executada ao vivo com CTV 232508 e valores conferidos)

### Passos lógicos

1. **Identificar a cotação** — input do vendedor: `Cod_filial` + `Num_docto` do CTV.
   Busca em `tbsaidas` com `Cod_docto='CTV'` e `Status<>'C'`. Retorna
   `Chave_fato` (chave da CTV) e `Cod_cli_for` (cliente).
2. **Listar itens já na cotação** — pra excluir da sugestão (não sugerir o que o cliente
   já pediu). Vem de `tbsaidasitem` da mesma chave.
3. **Montar histórico "última compra" do cliente** — pra cada produto que ele já
   comprou:
   - Fonte: `tbsaidasitem` + `tbsaidas` filtrado por `Cod_cli_for = <cliente>` e
     `Cod_docto IN ('NFL','PEV')`, `Status<>'C'`, `Valor_unitario > 0`.
   - Ranqueia com `ROW_NUMBER() OVER (PARTITION BY Cod_produto ORDER BY Data_movto DESC,
     Chave_fato DESC)` e pega `rn=1` — a linha mais recente.
   - **Por que NFL + PEV**: NFL é a nota fiscal emitida (venda efetivada); PEV é o
     pedido de venda aceito. Se filtrasse só NFL, cliente que só chega até PEV (ex.:
     HALLEY TRAILER, cliente 11535 — tem 323 CTVs e 2 PEVs mas 0 NFLs) ficaria fora.
     O valor unitário é o mesmo entre CTV → PEV → EXV → NFL (mesma venda gera 4
     documentos com valores iguais — confirmado).
4. **Buscar custo por produto na filial da cotação** — fonte oficial é
   `tbProdutoPcp.Valor_custo_unt` filtrado por `Cod_filial = <filial da CTV>` e
   `Valor_custo_unt > 0`.
   - **Nunca usar `tbentradasitem.Valor_unitario` como custo** — esse é preço bruto
     pago ao fornecedor sem impostos/frete. `tbProdutoPcp.Valor_custo_unt` é o custo
     calculado pelo SIGE (a tela de cadastro EST050 aba "Custo" exibe exatamente esse
     campo — conferido: produto 104626 filial 100 mostra R$ 7,13 na tela e o banco
     tem 7,13 nesse campo).
5. **Calcular preço promocional** = `ROUND(Valor_custo_unt * 1.30, 2)`.
   - Markup 30% sobre o custo.
6. **Calcular quantidade mínima** = `CEILING(100 / preco_promo)`.
   - Total mínimo = `qtde_min * preco_promo`. Sempre ≥ R$ 100 (piso configurável).
7. **Aplicar filtros:**
   - Item **não** pode estar na cotação atual.
   - `preco_promo < ultimo_valor_pago` (filtro matador — obrigatório).
   - `((ultimo - promo) / ultimo) * 100 >= 5.0` (economia mínima 5% — configurável).
8. **Aleatorizar** — `ORDER BY NEWID()` (SQL Server) ou `shuffle` no backend.
9. **Pegar TOP 10** — pode retornar menos se poucos candidatos.
10. **Ordenar exibição por economia % desc** — o item de maior desconto aparece no topo
    da tabela (essa ordenação é aplicada no frontend/backend após o corte de 10).

### Query SQL testada (SATLBASE, funcionando ao vivo)

```sql
DECLARE @Cod_filial CHAR(3) = '100'
DECLARE @Num_docto INT = 232508
DECLARE @Piso_valor_item DECIMAL(10,2) = 100.00
DECLARE @Economia_min_pct DECIMAL(4,1) = 5.0
DECLARE @Markup DECIMAL(4,2) = 1.30

;WITH ctv AS (
  SELECT s.Chave_fato AS Chave_ctv, s.Cod_cli_for
  FROM tbsaidas s WITH (NOLOCK)
  WHERE LTRIM(RTRIM(s.Cod_filial))=@Cod_filial
    AND s.Cod_docto='CTV' AND s.Num_docto=@Num_docto AND s.Status<>'C'
),
itens_ctv AS (
  SELECT DISTINCT LTRIM(RTRIM(si.Cod_produto)) AS Cod_produto
  FROM tbsaidasitem si WITH (NOLOCK)
  INNER JOIN ctv ON si.Chave_fato = ctv.Chave_ctv
),
hist_ranq AS (
  SELECT LTRIM(RTRIM(si.Cod_produto)) AS Cod_produto,
         si.Valor_unitario, si.Qtde_pri, s.Data_movto,
         ROW_NUMBER() OVER (PARTITION BY si.Cod_produto
                            ORDER BY s.Data_movto DESC, s.Chave_fato DESC) AS rn
  FROM tbsaidasitem si WITH (NOLOCK)
  INNER JOIN tbsaidas s WITH (NOLOCK) ON s.Chave_fato = si.Chave_fato
  INNER JOIN ctv ON s.Cod_cli_for = ctv.Cod_cli_for
  WHERE s.Cod_docto IN ('NFL','PEV') AND s.Status<>'C' AND si.Valor_unitario > 0
),
ultimo_pago AS (
  SELECT Cod_produto,
         Valor_unitario AS Ult_valor,
         Qtde_pri AS Ult_qtde,
         Data_movto AS Ult_data
  FROM hist_ranq WHERE rn = 1
),
custo AS (
  SELECT LTRIM(RTRIM(Cod_produto)) AS Cod_produto,
         Valor_custo_unt AS Custo_unit
  FROM tbProdutoPcp WITH (NOLOCK)
  WHERE LTRIM(RTRIM(Cod_filial))=@Cod_filial
    AND Valor_custo_unt > 0
),
candidatos AS (
  SELECT up.Cod_produto,
         RTRIM(p.Desc_produto_est) AS Descricao,
         up.Ult_valor, up.Ult_qtde, up.Ult_data,
         ca.Custo_unit,
         CAST(ca.Custo_unit * @Markup AS DECIMAL(10,2)) AS Preco_promo,
         CAST(CEILING(@Piso_valor_item / (ca.Custo_unit * @Markup)) AS INT) AS Qtde_min,
         CAST(CEILING(@Piso_valor_item / (ca.Custo_unit * @Markup)) * (ca.Custo_unit * @Markup)
              AS DECIMAL(10,2)) AS Total_min,
         CAST((up.Ult_valor - (ca.Custo_unit * @Markup)) AS DECIMAL(10,2)) AS Economia_unit,
         CAST(((up.Ult_valor - (ca.Custo_unit * @Markup)) / up.Ult_valor) * 100
              AS DECIMAL(6,1)) AS Economia_pct
  FROM ultimo_pago up
  INNER JOIN custo ca ON ca.Cod_produto = up.Cod_produto
  INNER JOIN tbproduto p WITH (NOLOCK)
         ON LTRIM(RTRIM(p.Cod_produto)) = up.Cod_produto
  LEFT JOIN itens_ctv ic ON ic.Cod_produto = up.Cod_produto
  WHERE ic.Cod_produto IS NULL
    AND (ca.Custo_unit * @Markup) < up.Ult_valor
    AND ((up.Ult_valor - (ca.Custo_unit * @Markup)) / up.Ult_valor) * 100 >= @Economia_min_pct
)
SELECT TOP 10 * FROM candidatos ORDER BY NEWID()
```

### Parâmetros configuráveis (default validado)

| Parâmetro | Default | Efeito |
|---|---|---|
| `@Markup` | `1.30` | Markup sobre custo (30%) |
| `@Piso_valor_item` | `100.00` | Piso do total mínimo por item |
| `@Economia_min_pct` | `5.0` | Descarta item se economia < 5% |

**Opção discutida e NÃO aplicada** (Renato optou por não usar por ora): corte de
recência (só considera última compra dos últimos N meses). Motivo pra rejeitar: a
Renato preferiu "matador ≥5%" como único filtro pra evitar zerar a lista em clientes
esporádicos. Se depois quiser aplicar, filtro seria
`AND up.Ult_data >= DATEADD(month, -18, GETDATE())`.

## 4. Caso real validado (bate 100% com o esperado)

**CTV 232508, filial 100, PEDREIRA CATEDRAL LTDA (`Cod_cli_for=12086`)**, data 16/07/2026.

Exemplo do produto 100004 (ARR ENCOSTO DT MB 1113):
- Última compra da PEDREIRA: R$ 3,00 em 04/07/2026 (via NFL)
- `tbProdutoPcp.Valor_custo_unt` filial 100 = R$ 2,04
- Preço promocional = 2,04 × 1,30 = **R$ 2,65** ← este valor bate exato com o "Valor
  Promocional" que o próprio SIGE mostra pro mesmo produto no relatório nativo. Confirma
  que a fonte de custo `tbProdutoPcp.Valor_custo_unt` é a correta.

10 itens que a fórmula retornou (mix aleatório, ordem por economia desc):

| # | Cod | Produto | Últ. compra | Preço promo | Qtd. mín | Economia |
|---|---|---|---|---|---|---|
| 1 | 100111 | PARAFUSO SEXTAVADO UNF RP 3/8×4 | R$ 5,00 | R$ 1,39 | 72 | −72% |
| 2 | 100083 | PORCA SEXTAVADA M12×1,50 (MB) | R$ 0,75 | R$ 0,35 | 285 | −53% |
| 3 | 106655 | BUCHA BARRA ESTAB. DT MB AXOR 2035 | R$ 35,00 | R$ 17,69 | 6 | −49% |
| 4 | 101817 | GRAMPO 1/2×75×200 C | R$ 16,00 | R$ 9,28 | 11 | −42% |
| 5 | 105273 | PARAFUSO SX MB PINO BALANÇA LUB FREE | R$ 62,00 | R$ 38,56 | 3 | −38% |
| 6 | 100422 | BUCHA MOLA DT FO CARGO | R$ 15,00 | R$ 10,00 | 11 | −33% |
| 7 | 102014 | GRAMPO 7/8×130×750 C SUSPENSOR | R$ 100,00 | R$ 74,09 | 2 | −26% |
| 8 | 102889 | CATRACA 28E CH14 TRUCK CARRETA | R$ 65,00 | R$ 50,22 | 2 | −23% |
| 9 | 100137 | PARAFUSO SEXTAVADO UNF RP 5/8×3 | R$ 3,60 | R$ 2,82 | 36 | −22% |
| 10 | 105060 | PINO CENTRAL M16×10 CD 8.8 | R$ 35,00 | R$ 27,26 | 4 | −22% |

## 5. Gotchas já pagos nesta sessão (não repetir)

- **`tbzProdutocusto_EST065` e `vwProdutoCusto_EST065` estão VAZIAS** (0 linhas). Só
  são populadas quando a rotina EST065 roda. A fonte usada em produção é
  `tbProdutoPcp` — essa está populada e é atualizada.
- **`tbentradasitem.Valor_unitario` NÃO é custo** — é preço bruto pago ao fornecedor
  na última compra, sem impostos/frete. Usei por engano no primeiro protótipo e o
  Renato pegou (produto 104626: valor era 5,44 na EC mas custo real é 7,13). Nunca
  mais.
- **`Cod_filial` e `Cod_local` são `char` com espaço de preenchimento** —
  sempre usar `LTRIM(RTRIM(...))` no filtro.
- **Cliente HALLEY TRAILER (11535) é o exemplo do caso "só cotador"** — tem 323 CTVs e
  0 NFLs. Se filtrar só NFL, ele nunca teria sugestão. Por isso o histórico usa
  `Cod_docto IN ('NFL','PEV')`.
- **Aleatoriedade de verdade** — `NEWID()` no SQL Server; se rodar o mesmo SQL 2 vezes
  no mesmo minuto, os itens vêm em ordem diferente.

## 6. Estado do mockup visual

Arquivo: `C:\Users\Renato\Downloads\sugestao-vendedor-mockup.html` (self-contained, abre
direto no Chrome, sem servidor).

**Versão atual** (última iteração aprovada pelo Renato):
- Topbar removida (sem logo, sem título "Sugestão de Itens", sem badge mockup).
- Header do documento em navy gradient com 4 blocos: Cliente / Cotação / Data /
  Vendedor.
- Espaçamento de 22px entre o header e a tabela.
- Tabela com 7 colunas — **# / Código / Produto / Última compra / Preço promocional /
  Economia / Qtd. mín.** — tudo centralizado, exceto Produto (esquerda).
- Última compra e Preço promocional com **mesma tipografia** (Archivo 20px, weight
  900) — diferença só na cor: cinza (`--ink-soft`) pra referência, verde
  (`--ok-fg`) pra oferta. Comparação visual direta.
- **Sem R$**, **sem "un"**, **sem "/un"** nos números — cliente já sabe o contexto.
- **Ordenação** por economia % desc (item de maior desconto no topo).
- Botão "Nova consulta" no canto superior direito (sem imprimir/PDF nesta versão —
  Renato optou por retirar; se voltar a ser necessário, é 1 botão a mais chamando
  `window.print()`).

Design system Napel aplicado (`C:\Users\Renato\scripts-clavis\PLAYBOOK-UI-UX-SENIOR.md`):
navy #0A3C5F, sky #74A9D7, verde ok #10B981, fontes Archivo (títulos, 700–900) + Inter
(corpo). Zero emoji. Ícones SVG inline (símbolo `#i-down` pra flecha da economia).

## 7. Arquitetura sugerida (mesma stack já aprovada pra outros módulos)

Seguir exatamente o padrão do módulo irmão **Comparador de Cotações Simples** — está em
produção no Clavis via iframe. Ver
`C:\Users\Renato\scripts-clavis\comparador-cotacoes-simples\CONTINUAR-AQUI.md` seções
"Arquitetura final (dual-mode + SSO + filial)".

Resumo do padrão:

- **Backend:** FastAPI + Python 3.14 (pyodbc no local, psycopg2 no Postgres compartilhado).
- **Dual-mode:**
  - `SUGESTAO_DB=satlbase` (local, PC do Renato) — consulta SATLBASE ao vivo.
  - `SUGESTAO_DB=postgres` (produção VPS) — consulta o Postgres compartilhado do Clavis
    com snapshot sincronizado a cada 6h via `sync_sugestao_vendedor.py` no PC.
- **Auth em produção:** iframe + JWT do Clavis via `postMessage` (padrão B2 já usado
  em Troca de Óleo e Comparador). Sem Basic Auth em produção.
- **Deploy:** Coolify (projeto "Demos"), FQDN `sugestao-vendedor.demos.napel.com.br`
  ou nome equivalente que o Renato aprovar.
- **Repo:** GitHub público (padrão dos apps standalone do Renato — senão o Coolify não
  puxa sem SSH key). **Nunca commitar segredo no repo** (senha do Postgres, tokens).
- **Dockerfile:** precisa ter `curl` instalado — Coolify roda healthcheck via
  `docker exec`.
- **Health endpoint:** `/health` (sem auth, sem tocar banco — retorna 200 em <10ms).

### Rotas mínimas do backend

- `POST /sugerir` — body `{filial, num_docto, params?}` → devolve lista de itens.
  Aceita bearer JWT do Clavis se produção; sem auth se local.
- `GET /health` — pra healthcheck do Coolify.
- `GET /` — serve o `index.html` estático (mockup).

### Sync (quando for pra produção)

`sync_sugestao_vendedor.py` roda no PC via Task Scheduler `LogonType=S4U`, a cada 6h.
Sincroniza pro Postgres compartilhado do Clavis:

- `historico_ultimo_pago` — snapshot da CTE `ultimo_pago` pra cada
  `(Cod_cli_for, Cod_produto)`.
- `custo_produto_filial` — snapshot de `tbProdutoPcp.Valor_custo_unt` por
  `(Cod_filial, Cod_produto)`.

Volumes esperados: `historico_ultimo_pago` tende a ficar grande (milhões de linhas,
cliente × produto). Ver se cabe fazer sync incremental ou se aceita sync full a cada 6h.
Sync full deve estar OK — o comparador irmão faz full toda vez.

## 8. Passos pra continuar

1. **Escolher nome final do módulo** (ex.: "Fechamento de Cotação", "Booster",
   "Sugestão Matadora"). Renato decide.
2. **Criar repo GitHub público** no padrão `renatonapel-arch/sugestao-vendedor`.
3. **Escrever `app.py`** com FastAPI + rota `POST /sugerir` chamando a query SQL da
   seção 3. Rodar local com `COMPARADOR_DB=satlbase` (pyodbc direto no SATLBASE).
4. **Ligar o mockup ao backend** — trocar o `POOL` fixo em JS por `fetch('/sugerir')`
   real.
5. **Testar local ponta-a-ponta** com filial 100 + CTVs reais.
6. **Escrever `sync_sugestao_vendedor.py`** e criar as 2 tabelas no Postgres
   compartilhado (`sugestao_vendedor.historico_ultimo_pago`,
   `sugestao_vendedor.custo_produto_filial`).
7. **Deploy Coolify** (mesmo padrão do comparador).
8. **Embutir no Clavis** — nova rota `/compras/sugestao-vendedor` no
   `frontend/src/pages/compras/sugestao-vendedor/index.tsx` com iframe + postMessage +
   entrada no `module.json`.
9. **Task Scheduler S4U** pra sync (pede elevação — Renato precisa rodar
   `criar-task-sync.ps1` como admin uma vez).

## 9. Arquivos

| O quê | Caminho |
|---|---|
| Mockup aprovado (visual + dados de exemplo) | `C:\Users\Renato\Downloads\sugestao-vendedor-mockup.html` |
| Logo Napel usada no header do PDF | `C:\Users\Renato\Downloads\logo-mark-t.svg` |
| Playbook UI/UX (obrigatório antes de mexer no visual) | `C:\Users\Renato\scripts-clavis\PLAYBOOK-UI-UX-SENIOR.md` |
| Playbook demo funcional | `C:\Users\Renato\scripts-clavis\PLAYBOOK-DEMO-FUNCIONAL.md` |
| Handoff do módulo irmão (padrão de arquitetura) | `C:\Users\Renato\scripts-clavis\comparador-cotacoes-simples\CONTINUAR-AQUI.md` |
| Pasta deste projeto (código vai aqui) | `C:\Users\Renato\scripts-clavis\sugestao-vendedor\` |
| PDF de referência do relatório SIGE nativo | `C:\Users\Renato\Downloads\232524.pdf` |

## 10. Conexão SATLBASE (senha e receita — já validada)

`.env` em `C:\Users\Renato\.claude\.env`, chave `AZURE_SQL_PASSWORD`. Servidor `SRV-BD`,
banco `SATLBASE`, usuário `clavis`. **Somente leitura** (`SELECT` + `WITH (NOLOCK)`,
nunca `INSERT/UPDATE/DELETE`).

PowerShell (validado, usado nesta sessão inteira):

```powershell
$EnvPath = 'C:\Users\Renato\.claude\.env'
$pwd = ((Get-Content $EnvPath -Encoding UTF8 | Where-Object { $_ -match '^\s*AZURE_SQL_PASSWORD\s*=' } | Select-Object -First 1) -replace '^\s*AZURE_SQL_PASSWORD\s*=\s*','').Trim()
$cs = "Server=SRV-BD;Database=SATLBASE;User Id=clavis;Password=$pwd;TrustServerCertificate=True;Connect Timeout=15;"
$conn = New-Object System.Data.SqlClient.SqlConnection $cs
$conn.Open()
```

Python (pyodbc — validado no módulo irmão):

```python
import pyodbc, os, pathlib
env = dict(l.strip().split('=', 1) for l in pathlib.Path(os.path.expanduser('~/.claude/.env')).read_text().splitlines() if '=' in l and not l.startswith('#'))
cn = pyodbc.connect(
    f"DRIVER={{SQL Server}};SERVER=SRV-BD;DATABASE=SATLBASE;"
    f"UID=clavis;PWD={env['AZURE_SQL_PASSWORD']};TrustServerCertificate=yes"
)
```

## 11. Regras do Renato pra respeitar

- **Nunca chutar** — provar cada valor no banco antes de afirmar (esta sessão inteira
  operou assim, inclusive descobrindo que 5,44 estava errado como custo).
- **Nunca commitar segredo em repo público** (padrão do módulo irmão, ver seção
  arquitetura).
- **Zero emoji no visual** — tudo SVG (playbook UI/UX sênior).
- **Layout enxuto** — Renato prioriza remover informação a adicionar. Se em dúvida,
  remove.
- **Comparação visual pareada** — quando 2 números precisam ser comparados (última
  compra × promocional), usa mesma tipografia e tamanho, muda só a cor.
