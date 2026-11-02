FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# O banco SQLite fica em /data (monte um volume/disco persistente aqui)
RUN mkdir -p /data
ENV DATABASE_URL=sqlite:////data/b3_monitor.db

EXPOSE 8000

# Um único worker: o agendador de relatórios e o limite de login ficam em memória.
# --proxy-headers: necessário atrás do proxy HTTPS da hospedagem (cookie Secure e IP real).
CMD ["sh", "-c", "uvicorn main:app --host 0.0.0.0 --port ${PORT:-8000} --workers 1 --proxy-headers --forwarded-allow-ips='*'"]
