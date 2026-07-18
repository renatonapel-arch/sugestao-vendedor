FROM python:3.12-slim

# Coolify checa saúde via curl DE DENTRO do container.
RUN apt-get update && apt-get install -y --no-install-recommends curl && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements-prod.txt .
RUN pip install --no-cache-dir -r requirements-prod.txt

COPY app.py historico_db.py ./
# postgres_backend.py será adicionado quando promover pra produção
COPY static ./static

ENV SUGESTAO_DB=postgres
EXPOSE 8000

CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000"]
