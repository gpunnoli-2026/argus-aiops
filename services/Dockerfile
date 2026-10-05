# One Dockerfile for every service: each is a main.py plus requirements.txt in
# its own directory. Build from this directory with the service as an argument:
#
#   docker build --build-arg SERVICE=anomaly-detector -t argus-anomaly-detector services/
FROM python:3.12-slim

ARG SERVICE

# Unprivileged, with a real home directory: mlflow and matplotlib (via prophet)
# both write caches under $HOME at startup.
RUN useradd --uid 10001 --create-home app
WORKDIR /app

# Dependencies first, so a code-only change reuses this layer — the prophet
# install alone is several minutes.
COPY ${SERVICE}/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY ${SERVICE}/main.py .

USER app
EXPOSE 8080
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8080"]
