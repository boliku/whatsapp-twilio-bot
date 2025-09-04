#!/bin/bash

# Script para deployar a Google Cloud Run

PROJECT_ID="whatsapp-bot-sentido-biologico"  # Cambia por tu project ID
SERVICE_NAME="whatsapp-bot"
REGION="southamerica-east1"
GITHUB_REPO="https://github.com/boliku/whatsapp-twilio-bot"  # Cambia por tu repo

echo "🚀 Deploying WhatsApp Bot to Google Cloud Run..."

# Deploy desde GitHub
gcloud run deploy $SERVICE_NAME \
  --source $GITHUB_REPO \
  --platform managed \
  --region $REGION \
  --allow-unauthenticated \
  --memory 1Gi \
  --cpu 1 \
  --max-instances 10 \
  --set-env-vars="WHATSAPP_SHEET_ID=1dG5r7l51rSV-c2rbQVfH-TQtxYtzY56xt-ZzLCCQou8" \
  --set-env-vars="WHATSAPP_SHEET_TAB=whatsapp_inbox" \
  --set-env-vars="LOCAL_TZ=America/Argentina/Buenos_Aires" \
  --set-env-vars="GOOGLE_CREDS_JSON=/tmp/credentials.json" \
  --set-secrets="TWILIO_ACCOUNT_SID=twilio-account-sid:latest" \
  --set-secrets="TWILIO_AUTH_TOKEN=twilio-auth-token:latest" \
  --set-secrets="/tmp/credentials.json=google-credentials:latest"

echo "✅ Deploy completed!"
echo "Your service URL will be displayed above."