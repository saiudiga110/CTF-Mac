FROM python:3.12-alpine AS builder

WORKDIR /wheels

RUN apk add --no-cache --virtual .build-deps \
    gcc \
    musl-dev \
    libffi-dev \
    openssl-dev \
    libxml2-dev \
    libxslt-dev

COPY requirements.txt .
RUN pip wheel --no-cache-dir --wheel-dir /wheels -r requirements.txt

FROM python:3.12-alpine

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

RUN apk add --no-cache bash libxml2 libxslt

COPY requirements.txt .
COPY --from=builder /wheels /wheels
RUN pip install --no-cache-dir --no-index --find-links=/wheels -r requirements.txt && \
    rm -rf /wheels

COPY . .

ARG TARGET_PROFILE=all
ENV TARGET_PROFILE=${TARGET_PROFILE}

RUN python init_db.py && \
    mkdir -p /root && echo "placeholder" > /root/flag.txt && \
    echo "placeholder" > /etc/vbank_internal.txt && \
    chmod +x /app/entrypoint.sh

EXPOSE 80

ENV FLAG_SECRET=vbank_ctf_default_2024
ENV TEAM_ID=0
ENV FLASK_SECRET=vbank_portal_dev_key_2024_xK9
ENV JWT_SECRET=vbank2024

CMD ["/app/entrypoint.sh"]
