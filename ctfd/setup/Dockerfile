FROM python:3.11-slim
WORKDIR /setup
COPY ctfd/setup/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY ctfd/setup/setup.py .
COPY ctfd/setup/challenge_catalog.py .
COPY challenge-catalog.json .
CMD ["python", "-u", "setup.py"]
