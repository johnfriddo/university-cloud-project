# Build context is services/, not services/notification-service/: the image
# needs the shared media-common package too.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY common/ /app/common/
RUN pip install --no-cache-dir /app/common

COPY notification-service/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

COPY notification-service/ /app/

RUN adduser --system --group --no-create-home app && chown -R app:app /app
USER app

# Only the health endpoints: the events arrive from the fanout exchange.
EXPOSE 8000

CMD ["python", "-m", "notifier.main"]
