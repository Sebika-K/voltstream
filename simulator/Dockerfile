FROM python:3.12-slim

# Send Python's print/log output straight to the container's log stream instead
# of buffering it, so `docker compose logs simulator` shows activity live.
ENV PYTHONUNBUFFERED=1

WORKDIR /app

# Dependencies first (their own layer), so editing simulator code doesn't force
# a re-download of httpx on every rebuild.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

CMD ["python", "main.py"]
