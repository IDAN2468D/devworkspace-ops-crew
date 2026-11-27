FROM python:3.12-slim

WORKDIR /app
COPY services/search_service/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

COPY shared ./shared
COPY services/__init__.py ./services/__init__.py
COPY services/search_service ./services/search_service

EXPOSE 8005
CMD ["uvicorn", "services.search_service.main:app", "--host", "0.0.0.0", "--port", "8005"]
