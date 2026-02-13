FROM python:3.12-slim

WORKDIR /app

RUN pip install --no-cache-dir fastapi uvicorn jinja2 python-multipart itsdangerous anthropic

COPY app/ .

EXPOSE 8000

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
