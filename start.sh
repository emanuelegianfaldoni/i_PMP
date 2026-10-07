#!/bin/bash
set -e

cd "$(dirname "$0")"

# Carica variabili d'ambiente
if [ ! -f .env ]; then
  echo "❌ File .env non trovato. Copia .env.example in .env e compilalo."
  exit 1
fi
export $(grep -v '^#' .env | grep -v '^$' | xargs)

# Verifica deployment ID configurati
if [ "$CHAT_DEPLOYMENT_ID" = "<da-compilare>" ] || [ -z "$CHAT_DEPLOYMENT_ID" ]; then
  echo "⚠️  CHAT_DEPLOYMENT_ID non configurato."
  echo "   Esegui prima: python setup_deployments.py"
  echo ""
  read -p "Vuoi eseguire setup_deployments.py adesso? (s/n) " choice
  if [ "$choice" = "s" ]; then
    python setup_deployments.py
    echo ""
    echo "Aggiorna il .env con i valori sopra e rilancia ./start.sh"
    exit 0
  else
    exit 1
  fi
fi

echo ""
echo "╔══════════════════════════════════════╗"
echo "║         i-PMP — Avvio sistema        ║"
echo "╚══════════════════════════════════════╝"
echo ""
echo "  Progetto : $PROJECT_ID"
echo "  Chat     : $CHAT_MODEL ($CHAT_DEPLOYMENT_ID)"
echo "  Embed    : $EMBED_MODEL ($EMBED_DEPLOYMENT_ID)"
echo "  HANA     : $HANA_HOST"
echo ""

# Avvia backend FastAPI in background
echo "🚀 Avvio backend (porta 8000)..."
python3 -m uvicorn app.main:app --host 0.0.0.0 --port 8000 --log-level info --workers 2 --limit-concurrency 10 --timeout-keep-alive 600 &
BACKEND_PID=$!

# Attendi che il backend sia pronto
echo "   Attendo che il backend sia pronto..."
for i in $(seq 1 20); do
  if curl -sf http://localhost:8000/health > /dev/null 2>&1; then
    echo "   ✅ Backend pronto"
    break
  fi
  sleep 1
done

# Avvia frontend Streamlit in background
echo "🎨 Avvio frontend Streamlit (porta 8501)..."
BACKEND_URL=http://localhost:8000 python3 -m streamlit run app/app.py \
  --server.port 8501 \
  --server.headless true \
  --server.address 0.0.0.0 \
  --browser.gatherUsageStats false &
FRONTEND_PID=$!

echo ""
echo "✅ i-PMP in esecuzione"
echo "   Backend  → http://localhost:8000"
echo "   Frontend → http://localhost:8501"
echo "   API docs → http://localhost:8000/docs"
echo ""
echo "   Premi Ctrl+C per fermare tutto"
echo ""

# Gestisce Ctrl+C: ferma entrambi i processi
trap "echo ''; echo 'Arresto...'; kill $BACKEND_PID $FRONTEND_PID 2>/dev/null; exit 0" INT TERM

wait $BACKEND_PID $FRONTEND_PID
