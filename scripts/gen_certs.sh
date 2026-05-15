#!/usr/bin/env bash
# Generate self-signed TLS certificates for BHELVIZ development.
# In production: use proper CA-issued certificates (Let's Encrypt or internal PKI).
set -euo pipefail

CERT_DIR="${1:-./certs}"
mkdir -p "$CERT_DIR"

echo "[BHELVIZ] Generating self-signed TLS certificate in $CERT_DIR ..."
openssl req -x509 -newkey rsa:4096 -sha256 -days 365 \
  -keyout "$CERT_DIR/server.key" \
  -out    "$CERT_DIR/server.crt" \
  -subj   "/C=IN/ST=Delhi/L=New Delhi/O=BHEL/CN=bhelviz.local" \
  -addext "subjectAltName=DNS:bhelviz.local,DNS:localhost,IP:127.0.0.1" \
  -nodes

chmod 600 "$CERT_DIR/server.key"
echo "[BHELVIZ] Certificate generated:"
echo "  Key:  $CERT_DIR/server.key"
echo "  Cert: $CERT_DIR/server.crt"
echo ""
echo "NOTE: This is a self-signed certificate for DEVELOPMENT ONLY."
echo "      Browsers will show a security warning. Add the cert to your"
echo "      browser's trusted roots or use HTTP (port 8000) for local dev."
