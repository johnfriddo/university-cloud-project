# Build context is services/, not services/worker-service/: the image needs the
# shared media-common package too.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY common/ /app/common/
# [mongo]: this service reads and writes the technical metadata, so it needs the
# optional MongoDB driver of the shared package.
RUN pip install --no-cache-dir "/app/common[mongo]"

COPY worker-service/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

COPY worker-service/ /app/

RUN adduser --system --group --no-create-home app && chown -R app:app /app
USER app

# Only the health endpoints: the worker receives its work from the queue.
EXPOSE 8000

CMD ["python", "-m", "worker.main"]
