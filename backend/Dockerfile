FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY backend/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
RUN useradd --create-home planner
RUN mkdir -p /data && chown planner:planner /data
COPY backend/app ./app
COPY scripts/issue_legacy_claim.py ./scripts/issue_legacy_claim.py
COPY tests ./tests
# Import-only smoke check: never invoke the issuer or connect to the database.
RUN python -c "import runpy; runpy.run_path('scripts/issue_legacy_claim.py', run_name='claim_cli_import_smoke')"
USER planner
CMD ["uvicorn", "app.api_server.main:app", "--host", "0.0.0.0", "--port", "8000", "--no-access-log"]
