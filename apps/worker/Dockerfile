FROM python:3.12-slim
WORKDIR /service
COPY apps/api/requirements.txt /service/requirements.txt
RUN pip install --no-cache-dir -r /service/requirements.txt psycopg[binary]
COPY apps/api/app /service/app
COPY apps/worker /service/worker
CMD ["python", "-m", "worker.worker"]
