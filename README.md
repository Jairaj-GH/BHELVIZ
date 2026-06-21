# BHELVIZ (development)

Quick start (dev):

1. Copy `.env.example` to `.env` and fill required keys.
2. Build and run the backend and NLP services with Docker Compose:

```bash
docker-compose up --build api nlp prometheus
```

3. Open the admin console at `https://localhost:9443` (self-signed certs for dev).

Tests:
- Lightweight tests are in `tests/` and can be run without installing dev deps by running Python from the inner project directory.

Convenience commands (from repo root):

```bash
make build     # build all images
make up        # start api, nlp, prometheus
make logs      # follow logs
make test      # run tests inside api container
```
