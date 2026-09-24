# Single-stage build. The dependency set is small and wheel-only, so a builder
# stage would add complexity without meaningfully shrinking the image.
FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Dependencies first, so edits to application code do not invalidate this layer.
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY app/ ./app/
COPY scripts/ ./scripts/
COPY tests/ ./tests/
COPY pyproject.toml ./

# The index is built at run time rather than baked in, so the image stays small
# and holds no API-derived data (docs/design-decisions.md D10).
RUN mkdir -p data/raw data/processed data/figures data/renders data/eval_runs \
    && chmod +x scripts/entrypoint.sh

# Non-root: nothing here needs privileges.
RUN useradd --create-home --uid 1000 appuser && chown -R appuser:appuser /app
USER appuser

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=40s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://localhost:8000/api/health', timeout=4).status == 200 else 1)"

ENTRYPOINT ["scripts/entrypoint.sh"]
CMD ["serve"]
