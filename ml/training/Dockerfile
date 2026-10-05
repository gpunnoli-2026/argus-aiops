# Training image for the one-shot Job (make train) and the nightly CronJob.
# Same base as the services: the detector unpickles what this saves, so the
# Python and mlflow versions have to match.
FROM python:3.12-slim

RUN useradd --uid 10001 --create-home app
WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY train_anomaly.py .

USER app
# Not /app: mlflow writes scratch files to the working directory.
WORKDIR /home/app
CMD ["python", "/app/train_anomaly.py"]
