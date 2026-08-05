"""Apuração de conversão — roda 1x/dia, mede quais itens sugeridos viraram venda.

Critério (validado contra 96 consultas reais em 04/08/2026):
  o item só conta como convertido se o cliente cotou/pediu ele pelo **preço
  promocional que a sugestão propôs** (tolerância de 2%). Sem esse filtro a
  métrica infla ~6x — o produto sugerido é do histórico do cliente, então ele
  recompra naturalmente pelo preço de sempre, sem relação com o módulo.
  Prova: produto 107442 reapareceu a R$ 705,00, que é exatamente o último
  preço que o cliente já pagava; a sugestão propunha R$ 641,13.

Janela: reavalia todo dia até 30 dias após a consulta, depois congela
(97% dos pedidos saem no mesmo dia da cotação, mas a cauda vai até 17 dias).

Escopo da busca: qualquer CTV/PEV do mesmo cliente na janela — não só a
cotação original. Motivo: 100% dos itens de PEV nascem de uma CTV (o SIGE
não deixa criar item direto no pedido), então quando o vendedor "só inclui
no pedido" ele na prática usou outra cotação do cliente.
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone

import httpx

TOLERANCIA_PCT = 0.02   # 2% — cobre arredondamento do vendedor
JANELA_DIAS = 30
MIN_HORAS = 24


def _bridge_url() -> str:
    v = os.environ.get("CAIXA_INTERNO_BRIDGE_URL")
    if not v:
        raise RuntimeError("CAIXA_INTERNO_BRIDGE_URL não configurada")
    return v.rstrip("/")


def _bridge_secret() -> str:
    v = os.environ.get("CAIXA_INTERNO_BRIDGE_SECRET")
    if not v:
        raise RuntimeError("CAIXA_INTERNO_BRIDGE_SECRET não configurada")
    return v


def _docs_do_cliente(filial: str, num_docto: int, cod_cli: int, desde: str) -> list[dict]:
    r = httpx.get(
        f"{_bridge_url()}/sugestao/conversao",
        params={"filial": filial, "num_docto": num_docto, "cod_cli": cod_cli,
                "desde": desde, "janela_dias": JANELA_DIAS},
        headers={"X-Bridge-Secret": _bridge_secret()},
        timeout=30.0,
    )
    r.raise_for_status()
    return r.json().get("itens") or []


def _incremental(item: dict) -> bool | None:
    """A venda foi realmente nova, ou só desconto no que o cliente já ia levar?

    Só dá pra afirmar se a sugestão registrou o perfil de recompra do item
    (ciclo médio + dias parado). Item fora do ciclo = ele não viria sozinho,
    a oferta trouxe. Item dentro do ciclo = ia comprar de qualquer jeito e a
    Napel só abriu mão de margem. Sugestões da regra antiga não têm esse dado
    e ficam como None (indeterminado)."""
    ciclo = item.get("ciclo_dias")
    parado = item.get("dias_parado")
    if not ciclo or parado is None:
        return None
    return parado > ciclo * 1.5


def _casar(itens_sugeridos: list[dict], docs: list[dict]) -> list[dict]:
    """Retorna 1 entrada por produto sugerido que foi vendido pelo preço da
    sugestão. Se o produto aparece em CTV e PEV, mantém o PEV (venda fechada)."""
    promo_por_cod = {
        str(i.get("cod", "")).strip(): float(i.get("promo") or 0)
        for i in itens_sugeridos
    }
    incr_por_cod = {
        str(i.get("cod", "")).strip(): _incremental(i)
        for i in itens_sugeridos
    }
    achados: dict[str, dict] = {}
    for d in docs:
        cod = str(d.get("cod", "")).strip()
        promo = promo_por_cod.get(cod)
        valor = d.get("valor")
        if not promo or valor is None:
            continue
        if abs(float(valor) - promo) / promo > TOLERANCIA_PCT:
            continue  # vendeu por outro preço — recompra natural, não conta
        entrada = {
            "cod": cod, "promo": promo, "valor": float(valor),
            "qtde": d.get("qtde"), "docto": d.get("docto"),
            "num_docto": d.get("num_docto"), "data_doc": None,
            "na_ctv_orig": d.get("na_ctv_orig"),
            "virou_pedido": d.get("docto") == "PEV",
            "incremental": incr_por_cod.get(cod),
        }
        data_br = d.get("data") or ""
        if len(data_br) == 10:  # dd/mm/aaaa -> aaaa-mm-dd
            dd, mm, aa = data_br.split("/")
            entrada["data_doc"] = f"{aa}-{mm}-{dd}"
        anterior = achados.get(cod)
        if anterior is None or (entrada["virou_pedido"] and not anterior["virou_pedido"]):
            achados[cod] = entrada
    return list(achados.values())


def apurar(historico_db, limite: int | None = None) -> dict:
    """Percorre as consultas pendentes e grava as conversões encontradas."""
    pendentes = historico_db.pendentes_apuracao(min_horas=MIN_HORAS, janela_dias=JANELA_DIAS)
    if limite:
        pendentes = pendentes[:limite]

    agora = datetime.now(timezone.utc)
    processadas = convertidos_total = fechadas = erros = 0

    for p in pendentes:
        try:
            ts = p["ts_utc"]
            desde = ts.strftime("%Y-%m-%d")
            docs = _docs_do_cliente(p["filial"], p["num_docto"], int(p["cod_cli"]), desde)
            convertidos = _casar(p["itens"], docs)
            fechar = (agora - ts) > timedelta(days=JANELA_DIAS)
            historico_db.gravar_conversoes(p["id"], convertidos, fechar)
            processadas += 1
            convertidos_total += len(convertidos)
            if fechar:
                fechadas += 1
        except Exception:
            erros += 1

    return {
        "pendentes": len(pendentes),
        "processadas": processadas,
        "itens_convertidos": convertidos_total,
        "consultas_fechadas": fechadas,
        "erros": erros,
    }
