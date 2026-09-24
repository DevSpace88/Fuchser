# ============================================================================
# Dockerfile für das Frontend (PRODUKTIONS-Build)
# ============================================================================
# Multi-Stage:
#   1) "builder" — installiert deps + baut die Vite-App -> dist/
#   2) "runner"  — nginx liefert dist/ aus UND proxied /api an das Backend.
#
# Der /api-Proxy ersetzt den Vite-Dev-Proxy (vite.config.ts -> server.proxy).
# Im Produktions-Compose erreicht dieser Container das Backend unter dem
# Compose-Service-Namen "backend" auf Port 8000.

# ----------------------------------------------------------------------------
# Stage 1: Builder
# ----------------------------------------------------------------------------
FROM node:22-alpine AS builder
WORKDIR /app

# Erst package-Dateien kopieren, dann installieren (besseres Docker-Caching).
COPY package.json package-lock.json* ./
RUN npm ci

# Quellcode kopieren und bauen. Ergebnis liegt in /app/dist.
COPY . .
# VITE_API_URL wird zur Build-Zeit eingebaut. In Produktion nutzen wir
# relative /api-Pfade + den nginx-Proxy, daher bleibt sie leer.
ENV VITE_API_URL=""
RUN npm run build

# ----------------------------------------------------------------------------
# Stage 2: nginx — statische Dateien + /api-Reverse-Proxy zum Backend
# ----------------------------------------------------------------------------
FROM nginx:alpine AS runner

# nginx-Konfiguration: SPA-Routing-Fallback (alle Pfade -> index.html)
# + /api-Proxy zum Backend (Compose-Service "backend", Port 8000).
# Der Proxy setzt Host/X-Forwarded-Header; path wird 1:1 durchgereicht
# (ohne trailing slash), damit /api/v1/... unverändert beim Backend ankommt.
RUN printf 'server {\n\
    listen 80;\n\
    location /api/ {\n\
        proxy_pass http://backend:8000;\n\
        proxy_http_version 1.1;\n\
        proxy_set_header Host $host;\n\
        proxy_set_header X-Real-IP $remote_addr;\n\
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;\n\
        proxy_set_header X-Forwarded-Proto $scheme;\n\
        proxy_set_header Upgrade $http_upgrade;\n\
        proxy_set_header Connection "upgrade";\n\
        proxy_read_timeout 300s;\n\
        proxy_buffering off;\n\
    }\n\
    location / {\n\
        root /usr/share/nginx/html;\n\
        index index.html;\n\
        try_files $uri $uri/ /index.html;\n\
    }\n\
}\n' > /etc/nginx/conf.d/default.conf

# Gebauten Code in den nginx-document-root kopieren.
COPY --from=builder /app/dist /usr/share/nginx/html

EXPOSE 80
CMD ["nginx", "-g", "daemon off;"]
