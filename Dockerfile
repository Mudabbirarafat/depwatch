FROM python:3.12-slim

WORKDIR /app

# Dependencies first so a code change does not invalidate the layer.
COPY pyproject.toml ./
COPY src ./src
RUN pip install --no-cache-dir ".[postgres]"

ENV PYTHONUNBUFFERED=1
ENV RAW_DIR=/data/raw

CMD ["python", "-m", "depwatch.cli", "runs"]
