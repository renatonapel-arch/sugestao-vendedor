"""Histórico de consultas — Postgres compartilhado do Clavis (prod).

Mesma API de historico_db.py (init/registrar/listar/obter) — app.py troca via
env SUGESTAO_HIST=postgres. Schema `sugestao_vendedor.historico_consultas`.

Env:
  SUGESTAO_DATABASE_URL   DSN psycopg2 completo. Vem do Coolify.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo

import psycopg2
import psycopg2.extras

_TZ_BR = ZoneInfo("America/Sao_Paulo")
_schema_ready = False


def _dsn() -> str:
    v = os.environ.get("SUGESTAO_DATABASE_URL")
    if not v:
        raise RuntimeError("SUGESTAO_DATABASE_URL não configurada")
    return v


def _connect():
    return psycopg2.connect(_dsn(), connect_timeout=10)


def init() -> None:
    global _schema_ready
    if _schema_ready:
        return
    with _connect() as conn, conn.cursor() as cur:
        cur.execute("""
            CREATE SCHEMA IF NOT EXISTS sugestao_vendedor;

            CREATE TABLE IF NOT EXISTS sugestao_vendedor.historico_consultas (
                id BIGSERIAL PRIMARY KEY,
                ts_utc TIMESTAMPTZ NOT NULL DEFAULT now(),
                vendedor TEXT NOT NULL,
                filial TEXT NOT NULL,
                num_docto INTEGER NOT NULL,
                cliente TEXT NOT NULL,
                snapshot_json JSONB NOT NULL
            );
            CREATE INDEX IF NOT EXISTS ix_hist_vendedor_ts
                ON sugestao_vendedor.historico_consultas (vendedor, ts_utc DESC);
            CREATE INDEX IF NOT EXISTS ix_hist_ts
                ON sugestao_vendedor.historico_consultas (ts_utc DESC);

            -- Apuracao de conversao (roda 1x/dia, reavalia ate 30d, depois congela)
            ALTER TABLE sugestao_vendedor.historico_consultas
                ADD COLUMN IF NOT EXISTS apurado_em TIMESTAMPTZ;
            ALTER TABLE sugestao_vendedor.historico_consultas
                ADD COLUMN IF NOT EXISTS apuracao_fechada BOOLEAN NOT NULL DEFAULT false;

            -- 1 linha por item sugerido que virou venda pelo preco da sugestao.
            -- Ausencia de linha = item nao converteu.
            CREATE TABLE IF NOT EXISTS sugestao_vendedor.conversao_item (
                consulta_id BIGINT NOT NULL
                    REFERENCES sugestao_vendedor.historico_consultas(id) ON DELETE CASCADE,
                cod_produto TEXT NOT NULL,
                promo_sugerido NUMERIC(14,4),
                valor_praticado NUMERIC(14,4),
                qtde NUMERIC(14,4),
                docto TEXT,
                num_docto INTEGER,
                data_doc DATE,
                na_ctv_orig BOOLEAN,
                virou_pedido BOOLEAN NOT NULL DEFAULT false,
                detectado_em TIMESTAMPTZ NOT NULL DEFAULT now(),
                PRIMARY KEY (consulta_id, cod_produto)
            );
            -- true  = venda nova (item estava fora do ciclo de recompra)
            -- false = cliente ja ia comprar; foi so desconto (canibalizacao)
            -- null  = sugerido pela regra antiga, sem dado de ciclo pra julgar
            ALTER TABLE sugestao_vendedor.conversao_item
                ADD COLUMN IF NOT EXISTS incremental BOOLEAN;
            CREATE INDEX IF NOT EXISTS ix_conv_consulta
                ON sugestao_vendedor.conversao_item (consulta_id);
        """)
        conn.commit()
    _schema_ready = True


def registrar(vendedor: str, filial: str, num_docto: int, cliente: str, snapshot: dict[str, Any]) -> int:
    init()
    with _connect() as conn, conn.cursor() as cur:
        cur.execute(
            "INSERT INTO sugestao_vendedor.historico_consultas "
            "(vendedor, filial, num_docto, cliente, snapshot_json) "
            "VALUES (%s, %s, %s, %s, %s) RETURNING id",
            (vendedor, filial, num_docto, cliente, json.dumps(snapshot, ensure_ascii=False)),
        )
        consulta_id = int(cur.fetchone()[0])
        conn.commit()
    return consulta_id


def _fmt_ts(ts_utc: datetime) -> tuple[str, str]:
    dt = ts_utc.astimezone(_TZ_BR)
    return dt.strftime("%d/%m/%Y"), dt.strftime("%H:%M")


def listar(vendedor: str | None, is_gestor: bool, limite: int = 100) -> list[dict[str, Any]]:
    init()
    with _connect() as conn, conn.cursor() as cur:
        if is_gestor:
            cur.execute(
                "SELECT id, ts_utc, vendedor, filial, num_docto, cliente, "
                "       jsonb_array_length(snapshot_json->'itens'), apurado_em "
                "FROM sugestao_vendedor.historico_consultas "
                "ORDER BY ts_utc DESC LIMIT %s",
                (limite,),
            )
        else:
            cur.execute(
                "SELECT id, ts_utc, vendedor, filial, num_docto, cliente, "
                "       jsonb_array_length(snapshot_json->'itens'), apurado_em "
                "FROM sugestao_vendedor.historico_consultas "
                "WHERE vendedor = %s ORDER BY ts_utc DESC LIMIT %s",
                (vendedor or "", limite),
            )
        rows = cur.fetchall()
    conv = conversao_por_consulta([r[0] for r in rows])
    out = []
    for r in rows:
        data, hora = _fmt_ts(r[1])
        c = conv.get(r[0])
        out.append({
            "id": r[0], "data": data, "hora": hora,
            "vendedor": r[2], "filial": r[3], "cotacao": r[4], "cliente": r[5],
            "itens_sugeridos": int(r[6] or 0),
            "convertidos": c["qtd"] if c else 0,
            "virou_pedido": c["virou_pedido"] if c else False,
            "apurado": r[7] is not None,
        })
    return out


def pendentes_apuracao(min_horas: int = 24, janela_dias: int = 30) -> list[dict[str, Any]]:
    """Consultas com mais de `min_horas` e ainda dentro da janela de apuracao."""
    init()
    with _connect() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT id, ts_utc, filial, num_docto,
                   snapshot_json->>'cod_cli_for' AS cod_cli,
                   snapshot_json->'itens' AS itens
            FROM sugestao_vendedor.historico_consultas
            WHERE apuracao_fechada = false
              AND ts_utc < now() - make_interval(hours => %s)
            ORDER BY ts_utc
            """,
            (min_horas,),
        )
        rows = cur.fetchall()
    out = []
    for r in rows:
        itens = r[5] if isinstance(r[5], list) else json.loads(r[5] or "[]")
        out.append({
            "id": r[0], "ts_utc": r[1], "filial": r[2], "num_docto": r[3],
            "cod_cli": r[4], "itens": itens,
        })
    return out


def gravar_conversoes(consulta_id: int, convertidos: list[dict[str, Any]], fechar: bool) -> None:
    """Substitui as conversoes da consulta (reapuracao e idempotente) e marca
    como apurada. `fechar=True` congela — nao reavalia mais."""
    init()
    with _connect() as conn, conn.cursor() as cur:
        cur.execute("DELETE FROM sugestao_vendedor.conversao_item WHERE consulta_id = %s", (consulta_id,))
        if convertidos:
            psycopg2.extras.execute_values(
                cur,
                """
                INSERT INTO sugestao_vendedor.conversao_item
                    (consulta_id, cod_produto, promo_sugerido, valor_praticado, qtde,
                     docto, num_docto, data_doc, na_ctv_orig, virou_pedido, incremental)
                VALUES %s
                """,
                [(consulta_id, c["cod"], c["promo"], c["valor"], c.get("qtde"),
                  c["docto"], c.get("num_docto"), c.get("data_doc"),
                  c.get("na_ctv_orig"), c.get("virou_pedido", False), c.get("incremental"))
                 for c in convertidos],
            )
        cur.execute(
            "UPDATE sugestao_vendedor.historico_consultas "
            "SET apurado_em = now(), apuracao_fechada = %s WHERE id = %s",
            (fechar, consulta_id),
        )
        conn.commit()


def conversao_por_consulta(ids: list[int]) -> dict[int, dict[str, Any]]:
    """{consulta_id: {qtd, produtos:[...], virou_pedido}} para as consultas dadas."""
    if not ids:
        return {}
    init()
    with _connect() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT consulta_id, cod_produto, virou_pedido, valor_praticado, qtde "
            "FROM sugestao_vendedor.conversao_item WHERE consulta_id = ANY(%s)",
            (ids,),
        )
        rows = cur.fetchall()
    out: dict[int, dict[str, Any]] = {}
    for cid, cod, pedido, valor, qtde in rows:
        d = out.setdefault(cid, {"qtd": 0, "produtos": [], "virou_pedido": False, "valor_total": 0.0})
        d["qtd"] += 1
        d["produtos"].append(cod)
        if pedido:
            d["virou_pedido"] = True
            if valor and qtde:
                d["valor_total"] += float(valor) * float(qtde)
    return out


def resumo_conversao(vendedor: str | None, is_gestor: bool) -> dict[str, Any]:
    """Numeros consolidados pra faixa do topo da aba Historico."""
    init()
    filtro = "" if is_gestor else "WHERE h.vendedor = %s"
    params: tuple = () if is_gestor else (vendedor or "",)
    with _connect() as conn, conn.cursor() as cur:
        cur.execute(
            f"""
            SELECT COUNT(*) AS consultas,
                   COALESCE(SUM(jsonb_array_length(h.snapshot_json->'itens')), 0) AS itens_sugeridos,
                   COUNT(*) FILTER (WHERE h.apurado_em IS NOT NULL) AS apuradas
            FROM sugestao_vendedor.historico_consultas h
            {filtro}
            """,
            params,
        )
        consultas, itens_sug, apuradas = cur.fetchone()

        filtro_c = "" if is_gestor else "WHERE h.vendedor = %s"
        cur.execute(
            f"""
            SELECT COUNT(*) AS itens_conv,
                   COUNT(DISTINCT c.consulta_id) AS consultas_conv,
                   COALESCE(SUM(c.valor_praticado * c.qtde) FILTER (WHERE c.virou_pedido), 0) AS valor,
                   COUNT(*) FILTER (WHERE c.incremental IS TRUE) AS itens_novos,
                   COALESCE(SUM(c.valor_praticado * c.qtde)
                            FILTER (WHERE c.virou_pedido AND c.incremental IS TRUE), 0) AS valor_novo,
                   COUNT(*) FILTER (WHERE c.incremental IS FALSE) AS itens_desconto
            FROM sugestao_vendedor.conversao_item c
            JOIN sugestao_vendedor.historico_consultas h ON h.id = c.consulta_id
            {filtro_c}
            """,
            params,
        )
        itens_conv, consultas_conv, valor, itens_novos, valor_novo, itens_desc = cur.fetchone()

    return {
        "consultas": int(consultas or 0),
        "itens_sugeridos": int(itens_sug or 0),
        "apuradas": int(apuradas or 0),
        "itens_convertidos": int(itens_conv or 0),
        "consultas_convertidas": int(consultas_conv or 0),
        "valor_gerado": float(valor or 0),
        "itens_novos": int(itens_novos or 0),
        "valor_novo": float(valor_novo or 0),
        "itens_desconto": int(itens_desc or 0),
    }


def ranking_vendedores(limite: int = 20) -> list[dict[str, Any]]:
    """Quem converte mais — base pra cobrança do time. Só faz sentido pro gestor."""
    init()
    with _connect() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT h.vendedor,
                   COUNT(DISTINCT h.id) AS consultas,
                   COALESCE(SUM(jsonb_array_length(h.snapshot_json->'itens')), 0) AS sugeridos,
                   COUNT(c.cod_produto) AS vendidos,
                   COALESCE(SUM(c.valor_praticado * c.qtde) FILTER (WHERE c.virou_pedido), 0) AS receita
            FROM sugestao_vendedor.historico_consultas h
            LEFT JOIN sugestao_vendedor.conversao_item c ON c.consulta_id = h.id
            GROUP BY h.vendedor
            ORDER BY receita DESC, vendidos DESC, consultas DESC
            LIMIT %s
            """,
            (limite,),
        )
        rows = cur.fetchall()
    return [{
        "vendedor": r[0],
        "consultas": int(r[1] or 0),
        "sugeridos": int(r[2] or 0),
        "vendidos": int(r[3] or 0),
        "receita": float(r[4] or 0),
    } for r in rows]


def obter(consulta_id: int, vendedor: str | None, is_gestor: bool) -> dict[str, Any] | None:
    init()
    with _connect() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT id, ts_utc, vendedor, filial, num_docto, cliente, snapshot_json "
            "FROM sugestao_vendedor.historico_consultas WHERE id = %s",
            (consulta_id,),
        )
        row = cur.fetchone()
    if not row:
        return None
    if not is_gestor and row[2] != (vendedor or ""):
        return None
    data, hora = _fmt_ts(row[1])
    snap = row[6] if isinstance(row[6], dict) else json.loads(row[6])
    snap["id"] = row[0]
    snap["data"] = data
    snap["hora"] = hora
    conv = conversao_por_consulta([row[0]]).get(row[0])
    snap["convertidos"] = conv["produtos"] if conv else []
    return snap
