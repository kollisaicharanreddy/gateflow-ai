#!/bin/bash
# deploy.sh — Run this on EC2 to pull latest code and redeploy
# Usage: bash deploy.sh

set -e

cd /home/ubuntu/gateflow

echo "=== Pulling latest code ==="
git pull origin main

echo "=== Rebuilding and restarting containers ==="
docker compose down
docker compose up -d --build

echo "=== Container status ==="
docker compose ps

echo "=== Done. Logs: docker compose logs -f ==="
