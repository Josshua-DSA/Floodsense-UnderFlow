# =============================================================
# FloodSense+ — Under-Flow Monitoring (HTTP) | GEMASTIK XVIII
# Flask + SQLite backend untuk dashboard monitoring banjir.
# Build:  docker build -t underflow-floodsense .
# Run:    docker run -p 5000:5000 underflow-floodsense
# =============================================================
FROM python:3.12-slim

# Keamanan & kebersihan dasar
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Install dependency lebih dulu (layer cache friendly)
COPY requirements.txt .
RUN pip install -r requirements.txt

# Salin source project
COPY app.py .
COPY templates/ templates/
COPY static/ static/
COPY data/ data/

# User non-root (praktik keamanan container)
# Direktori runtime untuk DB yang di-mount via named volume (persist antar restart)
RUN useradd --create-home appuser \
    && mkdir -p /app/runtime \
    && chown -R appuser:appuser /app
USER appuser

# SQLite butuh path yang writable (volume mount di compose)
EXPOSE 5000

# Healthcheck pakai endpoint /healthz
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:5000/healthz', timeout=4)" || exit 1

# Produksi: gunicorn (bukan dev server Flask).
# Init DB + auto-seed dari data/data_latih.csv jalan saat module di-import worker.
CMD ["gunicorn", "--bind", "0.0.0.0:5000", "--workers", "2", "--threads", "4", "--timeout", "60", "app:app"]
