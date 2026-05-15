#!/usr/bin/env bash
# BHELVIZ Development Environment Setup
# Run this once from the project root to get everything running.
set -euo pipefail

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; BLUE='\033[0;34m'; NC='\033[0m'

echo -e "${BLUE}╔══════════════════════════════════════════╗${NC}"
echo -e "${BLUE}║   BHELVIZ — Development Setup Script    ║${NC}"
echo -e "${BLUE}╚══════════════════════════════════════════╝${NC}"
echo ""

# ── Check prerequisites ───────────────────────────────────────────────────────
check_cmd() {
  if ! command -v "$1" &>/dev/null; then
    echo -e "${RED}✗ $1 is not installed. Please install it first.${NC}"
    exit 1
  fi
  echo -e "${GREEN}✓ $1 found${NC}"
}

echo "Checking prerequisites..."
check_cmd python3
check_cmd node
check_cmd npm

PYTHON_VERSION=$(python3 -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
NODE_VERSION=$(node --version | tr -d 'v' | cut -d. -f1)

if python3 -c "import sys; exit(0 if sys.version_info >= (3,11) else 1)"; then
  echo -e "${GREEN}✓ Python $PYTHON_VERSION >= 3.11${NC}"
else
  echo -e "${RED}✗ Python 3.11+ required, found $PYTHON_VERSION${NC}"
  exit 1
fi

if [ "$NODE_VERSION" -ge 18 ]; then
  echo -e "${GREEN}✓ Node $NODE_VERSION >= 18${NC}"
else
  echo -e "${RED}✗ Node 18+ required, found $NODE_VERSION${NC}"
  exit 1
fi

echo ""

# ── Backend setup ─────────────────────────────────────────────────────────────
echo -e "${BLUE}── Setting up backend ────────────────────────${NC}"

cd backend

if [ ! -d ".venv" ]; then
  echo "Creating Python virtual environment..."
  python3 -m venv .venv
fi

echo "Activating virtual environment and installing dependencies..."
source .venv/bin/activate
pip install --quiet --upgrade pip
pip install --quiet -r dev_requirements.txt
echo -e "${GREEN}✓ Backend dependencies installed${NC}"

# Generate JWT secret if not set
if [ -z "${BHELVIZ_JWT_SECRET:-}" ]; then
  export BHELVIZ_JWT_SECRET="$(python3 -c 'import secrets; print(secrets.token_hex(32))')"
  echo -e "${YELLOW}⚠  BHELVIZ_JWT_SECRET not set — using generated key (not persistent)${NC}"
  echo -e "   Add to your shell: export BHELVIZ_JWT_SECRET=\"$BHELVIZ_JWT_SECRET\""
fi

deactivate
cd ..

# ── Frontend setup ────────────────────────────────────────────────────────────
echo ""
echo -e "${BLUE}── Setting up frontend ───────────────────────${NC}"
cd frontend

if [ ! -f ".env" ]; then
  cp .env.example .env
  echo -e "${YELLOW}⚠  Created frontend/.env from .env.example${NC}"
  echo -e "   Edit frontend/.env and set VITE_ANTHROPIC_KEY=your-key for NLP features"
fi

npm install --silent
echo -e "${GREEN}✓ Frontend dependencies installed${NC}"
cd ..

echo ""
echo -e "${GREEN}╔══════════════════════════════════════════════════════════╗${NC}"
echo -e "${GREEN}║   Setup complete! Run the following in TWO terminals:   ║${NC}"
echo -e "${GREEN}╠══════════════════════════════════════════════════════════╣${NC}"
echo -e "${GREEN}║   Terminal 1 (backend):                                 ║${NC}"
echo -e "${GREEN}║   cd backend && source .venv/bin/activate               ║${NC}"
echo -e "${GREEN}║   export BHELVIZ_JWT_SECRET=<your-secret>               ║${NC}"
echo -e "${GREEN}║   uvicorn dev_main:app --reload --port 8000             ║${NC}"
echo -e "${GREEN}║                                                         ║${NC}"
echo -e "${GREEN}║   Terminal 2 (frontend):                                ║${NC}"
echo -e "${GREEN}║   cd frontend && npm run dev                            ║${NC}"
echo -e "${GREEN}║                                                         ║${NC}"
echo -e "${GREEN}║   Then open: http://localhost:5173                      ║${NC}"
echo -e "${GREEN}║   Admin:  admin@bhel.in / admin                         ║${NC}"
echo -e "${GREEN}║   User:   any@bhel.in / any6chars                       ║${NC}"
echo -e "${GREEN}╚══════════════════════════════════════════════════════════╝${NC}"
