from functools import lru_cache
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict
from typing import Literal

class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_ignore_empty=True, extra="ignore")
    database_url: str = "postgresql+psycopg://branchframe:branchframe@localhost:5436/branchframe"
    web_origin: str = "http://localhost:5173"
    auth_mode: Literal["demo"] = "demo"
    demo_mode: bool = True
    ai_mode: Literal["mock", "openai"] = "mock"
    # Generic OpenAI-compatible router configuration; provider/model are user-selected.
    ai_base_url: str = ""
    ai_api_key: str = ""
    ai_model: str = ""
    ai_provider_name: str = "router"
    ai_timeout_seconds: float = Field(default=60, gt=0, le=300)
    ai_max_output_tokens: int = Field(default=6000, ge=128, le=32000)
    ai_json_mode: bool = True
    ai_image_analysis_enabled: bool = False
    openai_api_key: str = ""
    openai_model: str = ""
    ai_max_assets_per_assessment: int = 20
    ai_monthly_budget_limit_usd: float | None = None
    api_public_base_url: str = "http://localhost:8000"
    storage_local_dir: str = "./.data/uploads"

@lru_cache
def get_settings() -> Settings:
    return Settings()
