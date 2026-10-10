FROM node:22-slim AS frontend
WORKDIR /build/frontend
COPY frontend/package*.json ./
RUN npm ci
COPY frontend/ ./
ARG LEDGERMIND_PUBLIC_BASE=/
ENV LEDGERMIND_PUBLIC_BASE=${LEDGERMIND_PUBLIC_BASE}
RUN npm run build

FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt requirements.lock ./
RUN pip install --no-cache-dir -r requirements.txt
COPY app/ ./app/
COPY data/import_templates/ ./data/import_templates/
COPY --from=frontend /build/frontend/dist/ ./frontend/dist/
COPY LICENSE NOTICE THIRD_PARTY_NOTICES.md ./
COPY docs/frontend-dependency-licenses.md ./docs/
RUN useradd --create-home --uid 10001 ledger && mkdir -p /var/lib/ledger && chown ledger:ledger /var/lib/ledger
ENV LEDGERMIND_DATA_DIR=/var/lib/ledger API_HOST=0.0.0.0 API_PORT=9543 AI_PROVIDER=mock LEDGERMIND_MODE=production
USER ledger
EXPOSE 9543
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:9543/api/health', timeout=4)"
CMD ["python", "-m", "app.main"]
