"""Acesso SATLBASE (somente leitura) para Sugestão de Itens.

Executa o SQL validado da CTV -> 10 sugestões matadoras.
Pattern idêntico ao módulo irmão (comparador-cotacoes-simples/satlbase.py).
"""
from __future__ import annotations

import os
import re
from datetime import datetime
from typing import Any

import pyodbc

_ENV_PATH = os.path.join(os.path.expanduser("~"), ".claude", ".env")


def _read_env_var(name: str) -> str:
    pattern = re.compile(rf"^\s*{re.escape(name)}\s*=\s*(.*)$")
    with open(_ENV_PATH, encoding="utf-8") as f:
        for line in f:
            m = pattern.match(line)
            if m:
                return m.group(1).strip()
    return ""


def _connect() -> pyodbc.Connection:
    pwd = _read_env_var("AZURE_SQL_PASSWORD")
    cs = (
        "DRIVER={SQL Server};SERVER=SRV-BD;DATABASE=SATLBASE;"
        f"UID=clavis;PWD={pwd};TrustServerCertificate=yes;Connection Timeout=15;"
    )
    return pyodbc.connect(cs, timeout=15)


_SQL_CTV_META = """
SELECT s.Chave_fato AS Chave_ctv,
       s.Cod_cli_for,
       s.Data_movto,
       RTRIM(cad.Nome_cadastro) AS Cliente,
       RTRIM(ISNULL(v.Nome_cadastro, '')) AS Vendedor
FROM tbsaidas s WITH (NOLOCK)
LEFT JOIN tbCadastroGeral cad WITH (NOLOCK) ON cad.Cod_cadastro = s.Cod_cli_for
LEFT JOIN tbCadastroGeral v WITH (NOLOCK) ON v.Cod_cadastro = s.Cod_vend_comp
WHERE LTRIM(RTRIM(s.Cod_filial)) = ?
  AND s.Cod_docto = 'CTV'
  AND s.Num_docto = ?
  AND s.Status <> 'C'
"""

_SQL_SUGESTOES = """
;WITH ctv AS (
  SELECT ? AS Chave_ctv, ? AS Cod_cli_for
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
  WHERE s.Cod_docto IN ('NFL','PEV') AND s.Status <> 'C' AND si.Valor_unitario > 0
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
  WHERE LTRIM(RTRIM(Cod_filial)) = ?
    AND Valor_custo_unt > 0
),
candidatos AS (
  SELECT up.Cod_produto,
         RTRIM(p.Desc_produto_est) AS Descricao,
         up.Ult_valor, up.Ult_qtde, up.Ult_data,
         ca.Custo_unit,
         CAST(ca.Custo_unit * ? AS DECIMAL(10,2)) AS Preco_promo,
         CAST(CEILING(? / (ca.Custo_unit * ?)) AS INT) AS Qtde_min,
         CAST((up.Ult_valor - (ca.Custo_unit * ?)) AS DECIMAL(10,2)) AS Economia_unit,
         CAST(((up.Ult_valor - (ca.Custo_unit * ?)) / up.Ult_valor) * 100
              AS DECIMAL(6,1)) AS Economia_pct
  FROM ultimo_pago up
  INNER JOIN custo ca ON ca.Cod_produto = up.Cod_produto
  INNER JOIN tbproduto p WITH (NOLOCK)
         ON LTRIM(RTRIM(p.Cod_produto)) = up.Cod_produto
  LEFT JOIN itens_ctv ic ON ic.Cod_produto = up.Cod_produto
  WHERE ic.Cod_produto IS NULL
    AND (ca.Custo_unit * ?) < up.Ult_valor
    AND ((up.Ult_valor - (ca.Custo_unit * ?)) / up.Ult_valor) * 100 >= ?
)
SELECT TOP 10 * FROM candidatos ORDER BY NEWID()
"""


def _fmt_date(dt: Any) -> str:
    if dt is None:
        return ""
    if isinstance(dt, datetime):
        return dt.strftime("%d/%m/%Y")
    return str(dt)


def sugerir(
    filial: str,
    num_docto: int,
    markup: float = 1.30,
    piso_valor_item: float = 100.00,
    economia_min_pct: float = 5.0,
) -> dict[str, Any]:
    """Busca uma CTV e devolve até 10 sugestões matadoras.

    Retorna:
        {
            "encontrada": bool,
            "cliente": str, "cod_cli_for": int,
            "vendedor": str, "data_ctv": "dd/mm/aaaa",
            "filial": str, "num_docto": int,
            "itens": [ { cod, desc, ult, ult_dt, promo, qmin, pct }, ... ]
        }
    """
    with _connect() as conn:
        cur = conn.cursor()
        cur.execute(_SQL_CTV_META, filial, num_docto)
        row = cur.fetchone()
        if not row:
            return {"encontrada": False, "erro": f"cotação CTV {filial}·{num_docto} não encontrada"}

        chave_ctv = row.Chave_ctv
        cod_cli_for = int(row.Cod_cli_for)
        data_ctv = _fmt_date(row.Data_movto)
        cliente = (row.Cliente or "").strip()
        vendedor_ctv = (row.Vendedor or "").strip()

        cur.execute(
            _SQL_SUGESTOES,
            chave_ctv, cod_cli_for,
            filial,
            markup,
            piso_valor_item, markup,
            markup, markup,
            markup, markup, economia_min_pct,
        )
        itens = []
        for r in cur.fetchall():
            itens.append({
                "cod": str(r.Cod_produto).strip(),
                "desc": (r.Descricao or "").strip(),
                "ult": float(r.Ult_valor),
                "ult_qtde": float(r.Ult_qtde) if r.Ult_qtde is not None else None,
                "ult_dt": _fmt_date(r.Ult_data),
                "custo": float(r.Custo_unit),
                "promo": float(r.Preco_promo),
                "qmin": int(r.Qtde_min),
                "econ_unit": float(r.Economia_unit),
                "pct": float(r.Economia_pct),
            })

    itens.sort(key=lambda x: x["pct"], reverse=True)

    return {
        "encontrada": True,
        "cliente": cliente,
        "cod_cli_for": cod_cli_for,
        "vendedor_ctv": vendedor_ctv,
        "data_ctv": data_ctv,
        "filial": filial,
        "num_docto": num_docto,
        "itens": itens,
    }
