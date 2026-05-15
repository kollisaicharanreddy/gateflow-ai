# GateFlow AI — Deployment Guide

## Architecture
```
Internet → EC2 (40.192.99.242)
              ├── Frontend  :80   (Nginx serving React build)
              ├── Backend   :8000 (FastAPI)
              └── RAG       :8001 (FastAPI + ChromaDB)

Files → S3 bucket: gateflow-uploads (ap-south-2)
DB    → Neon PostgreSQL (cloud)
Cache → Upstash Redis (cloud)
```

---

## One-time setup (do these before first deploy)

### 1. Create S3 bucket (from your local machine)
```bash
# Install AWS CLI if needed: https://docs.aws.amazon.com/cli/latest/userguide/install-cliv2.html
aws configure   # enter your AWS_ACCESS_KEY_ID and AWS_SECRET_ACCESS_KEY
bash deploy/create_s3_bucket.sh
```

### 2. Add production OAuth redirect URI in Google Console
Go to: https://console.cloud.google.com → APIs & Services → Credentials → your OAuth client
Add to "Authorized redirect URIs":
```
http://40.192.99.242:8000/auth/google/callback
```

### 3. EC2 Security Group — open these ports
In AWS Console → EC2 → Security Groups → Inbound rules, add:
| Port | Protocol | Source    |
|------|----------|-----------|
| 22   | TCP      | Your IP   |
| 80   | TCP      | 0.0.0.0/0 |
| 8000 | TCP      | 0.0.0.0/0 |
| 8001 | TCP      | 0.0.0.0/0 |

---

## First deploy on EC2

```bash
# 1. SSH into EC2
ssh -i your-key.pem ubuntu@40.192.99.242

# 2. Run setup script (installs Docker, Git, opens ports)
curl -o setup_ec2.sh https://raw.githubusercontent.com/YOUR_REPO/main/deploy/setup_ec2.sh
bash setup_ec2.sh

# 3. Log out and back in (docker group)
exit
ssh -i your-key.pem ubuntu@40.192.99.242

# 4. Clone your repo
git clone https://github.com/YOUR_REPO.git /home/ubuntu/gateflow
cd /home/ubuntu/gateflow

# 5. Build and start all containers
docker compose up -d --build

# 6. Check everything is running
docker compose ps
docker compose logs -f
```

---

## Redeploy after code changes

```bash
ssh -i your-key.pem ubuntu@40.192.99.242
cd /home/ubuntu/gateflow
bash deploy/deploy.sh
```

---

## Verify deployment

| URL | Expected |
|-----|----------|
| http://40.192.99.242 | React frontend |
| http://40.192.99.242:8000 | `{"app":"GateFlow AI",...}` |
| http://40.192.99.242:8000/docs | FastAPI Swagger UI |
| http://40.192.99.242:8001 | `{"status":"ok","service":"GateFlow AI"}` |

---

## Useful Docker commands

```bash
# View logs for a specific service
docker compose logs -f backend
docker compose logs -f rag
docker compose logs -f frontend

# Restart a single service
docker compose restart backend

# Stop everything
docker compose down

# Rebuild a single service
docker compose up -d --build backend
```

---

## Notes
- ChromaDB data persists in a Docker named volume `rag_db` — survives container restarts
- S3 files are served via presigned URLs (1-hour expiry) — no public bucket needed
- The RAG service is accessible on port 8001 — restrict this to internal only once you have a domain + Nginx
