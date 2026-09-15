FROM python:3.11-slim

WORKDIR /app

RUN pip install --no-cache-dir Flask==2.3.2 gunicorn==21.2.0 docker==7.2.0

COPY infra /app/infra

ENV PYTHONPATH=/app
EXPOSE 8088 8090

CMD ["gunicorn", "-b", "0.0.0.0:8088", "infra.instance_manager.app:app"]
