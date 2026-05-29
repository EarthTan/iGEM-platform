from __future__ import annotations

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    database_url: str = ""
    secret_key: str = ""
    algorithm: str = "HS256"
    access_token_expire_days: int = 7
    pipeline_root: str = "/app/iGEM-silk-main"
    output_root: str = "/app/output4"

    class Config:
        env_file = ".env"


settings = Settings()
