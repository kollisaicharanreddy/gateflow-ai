#!/bin/bash
# setup_ec2.sh — Run this ONCE on a fresh Ubuntu 22.04 EC2 instance
# Usage: bash setup_ec2.sh

set -e

echo "=== [1/4] Installing Docker ==="
sudo apt-get update -y
sudo apt-get install -y ca-certificates curl gnupg
sudo install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/ubuntu/gpg | sudo gpg --dearmor -o /etc/apt/keyrings/docker.gpg
sudo chmod a+r /etc/apt/keyrings/docker.gpg
echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] \
  https://download.docker.com/linux/ubuntu $(. /etc/os-release && echo "$VERSION_CODENAME") stable" \
  | sudo tee /etc/apt/sources.list.d/docker.list > /dev/null
sudo apt-get update -y
sudo apt-get install -y docker-ce docker-ce-cli containerd.io docker-compose-plugin
sudo usermod -aG docker $USER

echo "=== [2/4] Installing Git ==="
sudo apt-get install -y git

echo "=== [3/4] Opening firewall ports ==="
# Allow HTTP (80), Backend (8000), RAG (8001)
sudo ufw allow 22
sudo ufw allow 80
sudo ufw allow 8000
sudo ufw allow 8001
sudo ufw --force enable

echo "=== [4/4] Done ==="
echo ""
echo "NEXT STEPS:"
echo "  1. Log out and back in (so docker group takes effect)"
echo "  2. Clone your repo:  git clone <your-repo-url> /home/ubuntu/gateflow"
echo "  3. cd /home/ubuntu/gateflow"
echo "  4. Run:  docker compose up -d --build"
echo "  5. Check logs: docker compose logs -f"
