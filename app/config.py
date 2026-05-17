from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_prefix="JS_", extra="ignore")

    jellyfin_url: str = Field(default="http://127.0.0.1:8096")
    jellyfin_api_key: str = Field(default="")
    jellyfin_verify_ssl: bool = Field(default=False)
    jellyfin_db: Path = Field(default=Path("/data/jellyfin.db"))
    playback_db: Path = Field(default=Path("/data/playback_reporting.db"))
    cache_dir: Path = Field(default=Path("/cache"))
    session_secret: str = Field(default="")
    session_cookie_name: str = Field(default="jellysniff_session")
    session_max_age: int = Field(default=60 * 60 * 12)
    secure_cookie: bool = Field(default=True)
    trusted_proxies: str = Field(default="")
    enable_hsts: bool = Field(default=True)
    listen_host: str = Field(default="0.0.0.0")
    listen_port: int = Field(default=8095)
    brand_name: str = Field(default="JellySniff")
    accent_color: str = Field(default="#AA5CC3")

    @property
    def trusted_proxy_set(self) -> set[str]:
        return {p.strip() for p in self.trusted_proxies.split(",") if p.strip()}


_PLACEHOLDER_SECRETS = {"", "change-me-or-perish", "changeme", "secret"}


@lru_cache
def get_settings() -> Settings:
    s = Settings()
    if s.session_secret.strip() in _PLACEHOLDER_SECRETS or len(s.session_secret) < 24:
        raise RuntimeError(
            "JS_SESSION_SECRET is missing, blank, or too short. "
            "Generate one with `python -c 'import secrets;print(secrets.token_urlsafe(48))'` "
            "and set it in the environment before starting."
        )
    return s
