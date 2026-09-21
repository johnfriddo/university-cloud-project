# Build context is services/, not services/api-service/: the image needs the
# shared media-common package too.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# The shared package first: it changes far less often than the service code, so
# its layer stays cached across most rebuilds.
COPY common/ /app/common/
# [mongo]: this service reads the technical metadata to show them on the details
# page and to search the library by them.
RUN pip install --no-cache-dir "/app/common[mongo]"

COPY api-service/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

COPY api-service/ /app/

# Run as a non-root user: a process running as root would expose the host to
# unnecessary risk if the application were ever compromised. "app" is a system
# user with no home directory, and it owns /app so it can read its own code.
RUN adduser --system --group --no-create-home app && chown -R app:app /app
USER app

EXPOSE 8000

# Two workers is enough here: the api-service does no heavy lifting, it only
# registers the asset and publishes the job.
CMD ["gunicorn", "--bind", "0.0.0.0:8000", "--workers", "2", "--timeout", "30", \
     "--access-logfile", "-", "--error-logfile", "-", "wsgi:app"]
