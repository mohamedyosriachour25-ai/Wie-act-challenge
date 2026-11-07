# One image: the React app is built once, the FastAPI backend serves it. No credentials are baked in.
FROM node:22-alpine AS web
WORKDIR /web
COPY frontend/package*.json ./
RUN npm ci --no-audit --no-fund
COPY frontend/ ./
RUN npm run i18n:check && npm run build

FROM python:3.13-slim AS app
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 EDNOVA_ENV=production EDNOVA_DB=/data/ednova.db
RUN useradd --create-home --uid 10001 ednova && mkdir /data && chown ednova /data
WORKDIR /app/backend
COPY backend/requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
COPY backend/app ./app
COPY --from=web /web/dist /app/frontend/dist
USER ednova
VOLUME /data
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=4s --start-period=10s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if b'\"ok\":true' in urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=3).read() else 1)"
# --no-access-log: WebSocket URLs carry the session token in the query string, so the server's own request log stays off.
# EdNova writes its own access log (path and status only, secrets redacted).
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--no-access-log"]
