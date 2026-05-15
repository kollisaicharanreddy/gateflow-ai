FROM python:3.11-slim

WORKDIR /app

# PyMuPDF (pymupdf) ships its own MuPDF — no system lib needed.
# Only need build tools for packages that compile C extensions.
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Download NLTK punkt tokenizer at build time so first request is fast
RUN python -c "import nltk; nltk.download('punkt'); nltk.download('punkt_tab')"

COPY . .

# ChromaDB persists here — mount a named volume in production
RUN mkdir -p /app/gateflow_db

EXPOSE 8001

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8001"]
