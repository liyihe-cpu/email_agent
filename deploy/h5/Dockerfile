FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app/src

WORKDIR /app

COPY deploy/h5/requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt

# The public H5 container only needs the shared database/configuration layer
# and the Creator Pass API. Outreach, SMTP, IMAP, Milvus, and AI code stay out
# of this always-on web container.
COPY src/creator_mail_platform/__init__.py /app/src/creator_mail_platform/__init__.py
COPY src/creator_mail_platform/core /app/src/creator_mail_platform/core
COPY src/creator_mail_platform/creator_pass /app/src/creator_mail_platform/creator_pass
COPY frontend/creator_pass /app/frontend/creator_pass

EXPOSE 8000

CMD ["uvicorn", "creator_mail_platform.creator_pass.api:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "2", "--proxy-headers", "--forwarded-allow-ips", "*"]
