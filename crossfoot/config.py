import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    database_url: str
    mode: str


def get_settings() -> Settings:
    return Settings(
        database_url=os.environ.get(
            "CROSSFOOT_DATABASE_URL", "postgresql+psycopg://localhost/crossfoot"
        ),
        mode=os.environ.get("CROSSFOOT_MODE", "playground"),
    )
