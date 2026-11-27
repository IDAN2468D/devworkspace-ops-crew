FROM python:3.12-slim

WORKDIR /app
COPY services/hitl_service/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

COPY shared ./shared
COPY services/__init__.py ./services/__init__.py
COPY services/hitl_service ./services/hitl_service

RUN mkdir -p /app/data
EXPOSE 8002
CMD ["uvicorn", "services.hitl_service.main:app", "--host", "0.0.0.0", "--port", "8002"]
