FROM python:3.12-slim

WORKDIR /app

RUN pip install --no-cache-dir fastapi uvicorn jinja2 python-multipart itsdangerous anthropic Pillow

COPY app/ .

EXPOSE 8000

# Run as non-root user
RUN adduser --disabled-password --no-create-home --uid 1000 appuser
USER appuser

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
