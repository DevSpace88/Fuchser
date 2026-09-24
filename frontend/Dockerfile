# ============================================================================
# Dockerfile for the frontend (PRODUCTION build)
# ============================================================================
# Multi-stage:
#   1) "builder" — installs deps + builds the Vite app -> dist/
#   2) "runner"  — nginx serves dist/ AND proxies /api to the backend.
#
# The /api proxy replaces the Vite dev proxy (vite.config.ts -> server.proxy).
# In the production compose stack this container reaches the backend under the
# Compose service name "backend" on port 8000.

# ----------------------------------------------------------------------------
# Stage 1: Builder
# ----------------------------------------------------------------------------
FROM node:22-alpine AS builder
WORKDIR /app

# First copy the package files, then install (better Docker caching).
COPY package.json package-lock.json* ./
RUN npm ci

# Copy the source code and build. The result lives in /app/dist.
COPY . .
# VITE_API_URL is baked in at build time. In production we use
# relative /api paths + the nginx proxy, so it stays empty.
ENV VITE_API_URL=""
RUN npm run build

# ----------------------------------------------------------------------------
# Stage 2: nginx — static files + /api reverse proxy to the backend
# ----------------------------------------------------------------------------
FROM nginx:alpine AS runner

# nginx configuration: SPA routing fallback (all paths -> index.html)
# + /api proxy to the backend (Compose service "backend", port 8000).
# The proxy sets Host/X-Forwarded headers; the path is passed through 1:1
# (without a trailing slash) so /api/v1/... arrives at the backend unchanged.
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

# Copy the built code into the nginx document root.
COPY --from=builder /app/dist /usr/share/nginx/html

EXPOSE 80
CMD ["nginx", "-g", "daemon off;"]
