FROM python:3.12-slim

WORKDIR /app
COPY services/auth_service/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

COPY shared ./shared
COPY services/__init__.py ./services/__init__.py
COPY services/auth_service ./services/auth_service

RUN mkdir -p /app/data /app/secrets
EXPOSE 8001
CMD ["uvicorn", "services.auth_service.main:app", "--host", "0.0.0.0", "--port", "8001"]
