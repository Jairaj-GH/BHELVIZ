FROM python:3.11-slim

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

# Create non-root user
RUN useradd -m -u 1000 bhelviz

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

RUN chown -R bhelviz:bhelviz /app

USER bhelviz

EXPOSE 8443

# Run the app on 0.0.0.0:8443 — the FastAPI __main__ block expects SSL certs at /certs
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8443", "--log-level", "info"]
