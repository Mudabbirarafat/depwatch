"""Settings, read from the environment with usable defaults."""

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    database_url: str
    raw_dir: Path
    npm_registry: str
    request_timeout: float
    max_concurrency: int
    max_retries: int

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            # sqlite by default so the project runs with no services at all;
            # docker compose supplies a Postgres URL.
            database_url=os.environ.get("DATABASE_URL", "sqlite:///depwatch.db"),
            raw_dir=Path(os.environ.get("RAW_DIR", "data/raw")),
            npm_registry=os.environ.get("NPM_REGISTRY", "https://registry.npmjs.org"),
            request_timeout=float(os.environ.get("REQUEST_TIMEOUT", "20")),
            max_concurrency=int(os.environ.get("MAX_CONCURRENCY", "8")),
            max_retries=int(os.environ.get("MAX_RETRIES", "4")),
        )


settings = Settings.from_env()
