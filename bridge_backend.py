"""Backend prod: consulta SATLBASE via caixa-interno-bridge (Cloudflare quick tunnel).

Mesma interface de satlbase.py (função sugerir()) — app.py troca de backend
via env SUGESTAO_DB=bridge sem mudar orquestração.

Env:
  CAIXA_INTERNO_BRIDGE_URL     URL pública do quick tunnel (auto-atualizada pelo
                                tunnel-watcher.py rodando no PC do Renato).
  CAIXA_INTERNO_BRIDGE_SECRET  segredo compartilhado com a bridge (X-Bridge-Secret).
"""
from __future__ import annotations

import os
from typing import Any

import httpx


def _url() -> str:
    v = os.environ.get("CAIXA_INTERNO_BRIDGE_URL")
    if not v:
        raise RuntimeError("CAIXA_INTERNO_BRIDGE_URL não configurada")
    return v.rstrip("/")


def _secret() -> str:
    v = os.environ.get("CAIXA_INTERNO_BRIDGE_SECRET")
    if not v:
        raise RuntimeError("CAIXA_INTERNO_BRIDGE_SECRET não configurada")
    return v


def sugerir(
    filial: str,
    num_docto: int,
    markup: float = 1.30,
    piso_valor_item: float = 100.00,
    economia_min_pct: float = 5.0,
) -> dict[str, Any]:
    params = {
        "filial": filial,
        "num_docto": int(num_docto),
        "markup": markup,
        "piso": piso_valor_item,
        "econ_min": economia_min_pct,
    }
    headers = {"X-Bridge-Secret": _secret()}
    try:
        r = httpx.get(f"{_url()}/sugestao/candidatos", params=params, headers=headers, timeout=25.0)
    except httpx.HTTPError as exc:
        return {"encontrada": False, "erro": f"bridge indisponível: {exc}"}
    if r.status_code >= 500:
        return {"encontrada": False, "erro": f"bridge/SATLBASE falhou (HTTP {r.status_code})"}
    if r.status_code == 401:
        return {"encontrada": False, "erro": "X-Bridge-Secret inválido"}
    data = r.json()
    return data
