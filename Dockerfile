# SOPCheck API (FastAPI). Build from the repo root:  docker build -t sopcheck-api .
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 \
    APP_ENV=production DEMO_SEED=false COOKIE_SECURE=true \
    DATABASE_URL=sqlite:////data/app.db UPLOAD_DIR=/data/uploads PORT=8000

# OCR for scanned PDFs: tesseract + poppler (pdf2image)
RUN apt-get update && apt-get install -y --no-install-recommends tesseract-ocr tesseract-ocr-eng poppler-utils \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /srv
COPY backend/requirements.txt backend/requirements.txt
RUN pip install -r backend/requirements.txt "psycopg[binary]>=3.1"

# app code + versioned prompt + legal documents (served at /api/legal/*)
COPY backend/app backend/app
COPY prompts prompts
COPY legal legal

RUN useradd --create-home --uid 10001 app && mkdir -p /data/uploads && chown -R app /data
USER app
WORKDIR /srv/backend
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s CMD python -c "import urllib.request,os;urllib.request.urlopen(f'http://127.0.0.1:{os.environ.get(\"PORT\",\"8000\")}/api/health')"
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT} --proxy-headers --forwarded-allow-ips='*'"]
