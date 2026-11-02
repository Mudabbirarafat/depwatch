# Two stages, because the dashboard is a React build and the service is Python.
#
# The build lands inside the Python package so FastAPI serves the page and the
# API from one origin: no CORS, no second deployment to keep alive, and no way
# for the page and the API it calls to be different versions of each other.
#
# Node exists only in the first stage. The image that actually runs has a
# Python runtime and some static files, and never had a package manager for
# JavaScript installed in it.

# --- the dashboard ----------------------------------------------------------
FROM node:24-alpine AS frontend

# /app/frontend so that Vite's `outDir: ../src/depwatch/web` lands at
# /app/src/depwatch/web, mirroring where it belongs in the final image.
WORKDIR /app/frontend

# Manifests first: a source change must not invalidate the install layer.
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci

COPY frontend/ ./
# `npm run build` typechecks before it bundles, so a type error fails the image
# build rather than shipping a broken page.
RUN npm run build


# --- the service -------------------------------------------------------------
FROM python:3.12-slim

WORKDIR /app

# Dependencies first, for the same reason as above.
COPY pyproject.toml ./
COPY src ./src
COPY --from=frontend /app/src/depwatch/web ./src/depwatch/web
RUN pip install --no-cache-dir ".[postgres]"

ENV PYTHONUNBUFFERED=1
ENV RAW_DIR=/data/raw

# Shell form, because $PORT is assigned by the platform at runtime and the exec
# form would pass it through as a literal string.
CMD ["sh", "-c", "uvicorn depwatch.api:app --host 0.0.0.0 --port ${PORT:-8000}"]
