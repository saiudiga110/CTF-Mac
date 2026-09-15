FROM python:3.9-slim

WORKDIR /app

# System deps for lxml
RUN apt-get update && apt-get install -y --no-install-recommends \
    libxml2-dev libxslt1-dev gcc && \
    rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

ARG TARGET_PROFILE=all
ENV TARGET_PROFILE=${TARGET_PROFILE}

# Initialise the SQLite database (schema + seed data)
RUN python init_db.py

RUN mkdir -p /root && echo "placeholder" > /root/flag.txt
RUN echo "placeholder" > /etc/vbank_internal.txt

RUN chmod +x /app/entrypoint.sh

EXPOSE 80

# Runtime env vars — overridden by docker-compose / target-plugin per team
ENV FLAG_SECRET=vbank_ctf_default_2024
ENV TEAM_ID=0
ENV FLASK_SECRET=vbank_portal_dev_key_2024_xK9
ENV JWT_SECRET=vbank2024

CMD ["/app/entrypoint.sh"]
