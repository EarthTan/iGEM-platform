from __future__ import annotations
from pydantic_settings import BaseSettings


class SchedulerSettings(BaseSettings):
    database_url: str = ""
    pipeline_root: str = "/app/iGEM-silk-main"
    output_root: str = "/app/output4"
    poll_interval: int = 5
    heartbeat_interval: int = 60
    round_timeout: int = 21600

    class Config:
        env_file = ".env"


settings = SchedulerSettings()
