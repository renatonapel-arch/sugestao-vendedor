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
                "SELECT id, ts_utc, vendedor, filial, num_docto, cliente "
                "FROM sugestao_vendedor.historico_consultas "
                "ORDER BY ts_utc DESC LIMIT %s",
                (limite,),
            )
        else:
            cur.execute(
                "SELECT id, ts_utc, vendedor, filial, num_docto, cliente "
                "FROM sugestao_vendedor.historico_consultas "
                "WHERE vendedor = %s ORDER BY ts_utc DESC LIMIT %s",
                (vendedor or "", limite),
            )
        rows = cur.fetchall()
    out = []
    for r in rows:
        data, hora = _fmt_ts(r[1])
        out.append({
            "id": r[0], "data": data, "hora": hora,
            "vendedor": r[2], "filial": r[3], "cotacao": r[4], "cliente": r[5],
        })
    return out


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
    return snap
