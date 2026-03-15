#!/bin/bash
# deploy.sh — Automated Cloud Deployment for Safe & Healthy Chef
# Deploys orchestrator to Google Cloud Run
# Run: ./deploy.sh

set -e

PROJECT_ID=$(gcloud config get-value project)
IMAGE="gcr.io/$PROJECT_ID/safe-healthy-chef"
SERVICE="safe-healthy-chef"
REGION="us-central1"

echo "========================================"
echo "  Safe & Healthy Chef — Cloud Deploy"
echo "========================================"
echo "Project : $PROJECT_ID"
echo "Image   : $IMAGE"
echo "Service : $SERVICE"
echo "Region  : $REGION"
echo ""

echo "Building container image..."
gcloud builds submit --tag $IMAGE

echo "Deploying to Cloud Run..."
gcloud run deploy $SERVICE \
  --image $IMAGE \
  --platform managed \
  --region $REGION \
  --allow-unauthenticated \
  --set-env-vars GEMINI_API_KEY=$GEMINI_API_KEY

echo ""
echo "✅ Deployed successfully!"
echo "URL: $(gcloud run services describe $SERVICE --region $REGION --format 'value(status.url)')"
