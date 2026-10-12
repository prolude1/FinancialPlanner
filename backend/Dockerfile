ARG PYTHON_BASE=python:3.12-slim-trixie@sha256:02108f5d322dd89f1c9e552442c25acb0543dfdbc455693a5599624f20d9155d

FROM ${PYTHON_BASE} AS dependencies

ENV PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /build

RUN apt-get update \
    && apt-get upgrade --yes \
    && rm -rf /var/lib/apt/lists/*

COPY backend/requirements.txt ./requirements.txt
RUN pip wheel --no-cache-dir --wheel-dir=/wheels -r requirements.txt

FROM ${PYTHON_BASE} AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /app

RUN apt-get update \
    && apt-get upgrade --yes \
    && rm -rf /var/lib/apt/lists/*

COPY --from=dependencies /wheels /wheels
COPY backend/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir --no-index --find-links=/wheels -r requirements.txt \
    && rm -rf /wheels /root/.cache

RUN useradd --create-home planner \
    && mkdir -p /data \
    && chown planner:planner /data

COPY backend/app ./app
COPY scripts/issue_legacy_claim.py ./scripts/issue_legacy_claim.py
COPY tests ./tests

# Import-only smoke check: never invoke the issuer or connect to the database.
RUN python -c "import runpy; runpy.run_path('scripts/issue_legacy_claim.py', run_name='claim_cli_import_smoke')"

USER planner
CMD ["uvicorn", "app.api_server.main:app", "--host", "0.0.0.0", "--port", "8000", "--no-access-log"]
