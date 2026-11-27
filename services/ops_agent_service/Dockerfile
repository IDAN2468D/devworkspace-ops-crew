FROM python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    curl gnupg ca-certificates build-essential \
    && curl -fsSL https://deb.nodesource.com/setup_20.x | bash - \
    && apt-get install -y --no-install-recommends nodejs \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY services/ops_agent_service/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

COPY shared ./shared
COPY services/__init__.py ./services/__init__.py
COPY services/ops_agent_service ./services/ops_agent_service

RUN mkdir -p /app/secrets
EXPOSE 8004
CMD ["uvicorn", "services.ops_agent_service.main:app", "--host", "0.0.0.0", "--port", "8004"]
