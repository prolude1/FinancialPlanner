FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY backend/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
RUN useradd --create-home planner
RUN mkdir -p /data && chown planner:planner /data
COPY backend/app ./app
COPY tests ./tests
USER planner
CMD ["uvicorn", "app.api_server.main:app", "--host", "0.0.0.0", "--port", "8000", "--no-access-log"]
