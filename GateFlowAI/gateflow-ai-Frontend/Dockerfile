# Stage 1 — build
FROM node:20-alpine AS builder

WORKDIR /app
COPY package*.json ./
RUN npm ci

COPY . .
# Build args injected at docker build time
ARG VITE_API_BASE_URL
ARG VITE_RAG_BASE_URL
ARG VITE_WS_BASE_URL
ARG VITE_APP_ENV=production
ARG VITE_MOCK_MODE=false

RUN npm run build

# Stage 2 — serve with Nginx
FROM nginx:alpine

COPY --from=builder /app/dist /usr/share/nginx/html

# SPA fallback — all routes serve index.html
RUN printf 'server {\n\
    listen 80;\n\
    root /usr/share/nginx/html;\n\
    index index.html;\n\
    location / {\n\
        try_files $uri $uri/ /index.html;\n\
    }\n\
}\n' > /etc/nginx/conf.d/default.conf

EXPOSE 80
