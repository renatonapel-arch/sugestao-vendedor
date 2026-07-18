"""Sugestão de Itens pro Vendedor — API FastAPI.

Fluxo:
  vendedor manda (filial, num_docto) -> SATLBASE devolve até 10 itens matadores
  -> registra 1 linha no histórico local -> devolve JSON pro mockup.

Rotas:
  GET  /              -> serve static/index.html (mockup + JS que consome API)
  GET  /health        -> healthcheck raso (não toca banco)
  POST /sugerir       -> corpo: filial, num_docto, [vendedor, params] -> 10 itens
  GET  /historico     -> lista consultas (vendedor vê as próprias; gestor todas)
  GET  /historico/{id}-> reexibe o snapshot da consulta

Perfil (vendedor/gestor) + nome do vendedor:
  Local: via query/form/header `X-Vendedor` e `X-Role` (mockup passa).
  Produção: extraído do JWT do Clavis (email/nome + role).
"""
from __future__ import annotations

import os
import secrets

from fastapi import Depends, FastAPI, Form, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.staticfiles import StaticFiles
from jose import JWTError, jwt as jose_jwt

DB_BACKEND = os.environ.get("SUGESTAO_DB", "satlbase")
if DB_BACKEND == "bridge":
    import bridge_backend as db
else:
    import satlbase as db

HIST_BACKEND = os.environ.get("SUGESTAO_HIST", "sqlite")
if HIST_BACKEND == "postgres":
    import historico_pg as historico_db
else:
    import historico_db

APP_VERSION = "v1"

ROOT = os.path.dirname(os.path.abspath(__file__))
STATIC = os.path.join(ROOT, "static")

app = FastAPI(title="Sugestão de Itens")
app.mount("/static", StaticFiles(directory=STATIC), name="static")

historico_db.init()

_basic = HTTPBasic(auto_error=False)


def _load_basic_auth_users() -> dict[str, str]:
    users = {}
    user = os.environ.get("BASIC_AUTH_USER")
    pwd = os.environ.get("BASIC_AUTH_PASS")
    if user and pwd:
        users[user] = pwd
    for pair in os.environ.get("BASIC_AUTH_EXTRA", "").split(","):
        if ":" in pair:
            u, p = pair.split(":", 1)
            users[u.strip()] = p.strip()
    return users


def _verify_clavis_jwt(token: str) -> dict | None:
    secret = os.environ.get("CLAVIS_SECRET_KEY")
    if not secret:
        return None
    try:
        return jose_jwt.decode(token, secret, algorithms=["HS256"])
    except JWTError:
        return None


def require_auth(
    request: Request,
    credentials: HTTPBasicCredentials | None = Depends(_basic),
) -> dict:
    """Retorna dict: {vendedor, role}. Role ∈ {'vendedor','gestor'}.

    Ordem: (1) JWT Clavis via Bearer — (2) Basic Auth — (3) sem gate (dev local).
    Em local, o mockup manda header `X-Vendedor` / `X-Role` como identidade.
    """
    auth_header = request.headers.get("authorization", "")
    if auth_header.startswith("Bearer "):
        payload = _verify_clavis_jwt(auth_header[7:])
        if not payload:
            raise HTTPException(status_code=401, detail="token Clavis inválido")
        vendedor = (payload.get("nome") or payload.get("email") or "").upper()
        role = "gestor" if payload.get("role") in ("gestor", "admin") else "vendedor"
        return {"vendedor": vendedor, "role": role}

    users = _load_basic_auth_users()
    if users:
        if credentials is None:
            raise HTTPException(status_code=401, detail="login necessário", headers={"WWW-Authenticate": "Basic"})
        esperado = users.get(credentials.username)
        if not esperado or not secrets.compare_digest(credentials.password, esperado):
            raise HTTPException(status_code=401, detail="usuário/senha inválidos", headers={"WWW-Authenticate": "Basic"})
        return {"vendedor": credentials.username.upper(), "role": "vendedor"}

    if os.environ.get("CLAVIS_SECRET_KEY"):
        raise HTTPException(status_code=401, detail="autenticação necessária")

    # dev local — sem gate
    vendedor = (request.headers.get("x-vendedor") or "LOCAL").upper()
    role = (request.headers.get("x-role") or "vendedor").lower()
    if role not in ("vendedor", "gestor"):
        role = "vendedor"
    return {"vendedor": vendedor, "role": role}


@app.get("/health")
def health():
    return {"status": "ok", "version": APP_VERSION,
            "db_backend": DB_BACKEND, "hist_backend": HIST_BACKEND}


@app.get("/")
def index():
    return FileResponse(os.path.join(STATIC, "index.html"))


@app.post("/sugerir")
def sugerir(
    filial: str = Form(...),
    num_docto: int = Form(...),
    markup: float = Form(1.30),
    piso: float = Form(100.00),
    econ_min: float = Form(5.0),
    user: dict = Depends(require_auth),
):
    try:
        resultado = db.sugerir(
            filial=filial.strip(),
            num_docto=int(num_docto),
            markup=markup,
            piso_valor_item=piso,
            economia_min_pct=econ_min,
        )
    except Exception as exc:
        return JSONResponse({"erro": f"falha ao consultar SATLBASE: {exc}"}, status_code=500)

    if not resultado.get("encontrada"):
        return JSONResponse(
            {"erro": resultado.get("erro", "cotação não encontrada"), "encontrada": False},
            status_code=404,
        )

    consulta_id = historico_db.registrar(
        vendedor=user["vendedor"],
        filial=resultado["filial"],
        num_docto=resultado["num_docto"],
        cliente=resultado["cliente"],
        snapshot=resultado,
    )
    resultado["consulta_id"] = consulta_id
    resultado["vendedor_atual"] = user["vendedor"]
    return JSONResponse(resultado)


@app.get("/historico")
def historico(user: dict = Depends(require_auth)):
    linhas = historico_db.listar(user["vendedor"], user["role"] == "gestor")
    return JSONResponse({
        "role": user["role"],
        "vendedor_atual": user["vendedor"],
        "registros": linhas,
    })


@app.get("/historico/{consulta_id}")
def historico_detalhe(consulta_id: int, user: dict = Depends(require_auth)):
    snap = historico_db.obter(consulta_id, user["vendedor"], user["role"] == "gestor")
    if snap is None:
        raise HTTPException(status_code=404, detail="consulta não encontrada")
    return JSONResponse(snap)


if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", "9108"))
    uvicorn.run(app, host="127.0.0.1", port=port)
