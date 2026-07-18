"""Histórico de consultas — SQLite local.

Cada POST /sugerir bem-sucedido registra 1 linha. GET /historico devolve
a listagem filtrada por vendedor (ou todos, se gestor). GET /historico/{id}
devolve o snapshot completo dos 10 itens para reexibir o mesmo detalhamento.

Timezone: registra em UTC no banco; converte para America/Sao_Paulo na leitura.
"""
from __future__ import annotations

import json
import os
import sqlite3
from datetime import datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo

_TZ_BR = ZoneInfo("America/Sao_Paulo")
_DB_PATH = os.environ.get(
    "SUGESTAO_DB_PATH",
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "historico.db"),
)


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(_DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init() -> None:
    with _connect() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS historico_consultas (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts_utc TEXT NOT NULL,
                vendedor TEXT NOT NULL,
                filial TEXT NOT NULL,
                num_docto INTEGER NOT NULL,
                cliente TEXT NOT NULL,
                snapshot_json TEXT NOT NULL
            )
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS ix_historico_vendedor_ts
            ON historico_consultas (vendedor, ts_utc DESC)
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS ix_historico_ts
            ON historico_consultas (ts_utc DESC)
        """)


def registrar(vendedor: str, filial: str, num_docto: int, cliente: str, snapshot: dict[str, Any]) -> int:
    ts_utc = datetime.now(timezone.utc).isoformat()
    with _connect() as conn:
        cur = conn.execute(
            "INSERT INTO historico_consultas (ts_utc, vendedor, filial, num_docto, cliente, snapshot_json) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (ts_utc, vendedor, filial, num_docto, cliente, json.dumps(snapshot, ensure_ascii=False)),
        )
        return int(cur.lastrowid)


def _fmt_ts(ts_utc: str) -> tuple[str, str]:
    dt = datetime.fromisoformat(ts_utc).astimezone(_TZ_BR)
    return dt.strftime("%d/%m/%Y"), dt.strftime("%H:%M")


def listar(vendedor: str | None, is_gestor: bool, limite: int = 100) -> list[dict[str, Any]]:
    with _connect() as conn:
        if is_gestor:
            rows = conn.execute(
                "SELECT id, ts_utc, vendedor, filial, num_docto, cliente "
                "FROM historico_consultas ORDER BY ts_utc DESC LIMIT ?",
                (limite,),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT id, ts_utc, vendedor, filial, num_docto, cliente "
                "FROM historico_consultas WHERE vendedor = ? ORDER BY ts_utc DESC LIMIT ?",
                (vendedor or "", limite),
            ).fetchall()
    out = []
    for r in rows:
        data, hora = _fmt_ts(r["ts_utc"])
        out.append({
            "id": r["id"],
            "data": data,
            "hora": hora,
            "vendedor": r["vendedor"],
            "filial": r["filial"],
            "cotacao": r["num_docto"],
            "cliente": r["cliente"],
        })
    return out


def obter(consulta_id: int, vendedor: str | None, is_gestor: bool) -> dict[str, Any] | None:
    with _connect() as conn:
        row = conn.execute(
            "SELECT id, ts_utc, vendedor, filial, num_docto, cliente, snapshot_json "
            "FROM historico_consultas WHERE id = ?",
            (consulta_id,),
        ).fetchone()
    if not row:
        return None
    if not is_gestor and row["vendedor"] != (vendedor or ""):
        return None
    data, hora = _fmt_ts(row["ts_utc"])
    snapshot = json.loads(row["snapshot_json"])
    snapshot["id"] = row["id"]
    snapshot["data"] = data
    snapshot["hora"] = hora
    return snapshot
