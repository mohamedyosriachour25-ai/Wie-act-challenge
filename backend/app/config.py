"""Runtime settings. Everything that differs between dev and the challenge
(base URL, key, model names) comes from the environment: nothing is hard-coded."""
from __future__ import annotations

import dataclasses
import os
from dataclasses import dataclass
from pathlib import Path


def _load_dotenv() -> None:
    path = Path(__file__).resolve().parents[1] / ".env"
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


@dataclass(frozen=True)
class Settings:
    # "mock" runs the built-in fake EdNova service in-process (development and demo).
    base_url: str = "mock"
    api_key: str = "dev-mock-key"
    compact_model: str = "compact-1b"
    mid_model: str = "mid-8b"
    embed_model: str = "embed-small"
    min_similarity: float = 0.1       # below this a passage is not considered relevant (tune for the real embeddings)
    max_concurrent: int = 2           # the service caps concurrent requests per key
    cache_ttl_s: int = 6 * 3600
    max_quota_wait_s: float = 45.0    # longer than this and we tell the user instead of blocking
    db_path: str = "ednova.db"
    secret: str = "dev-secret-change-me"
    mock_latency_scale: float = 1.0
    live_capacity: int = 200
    env: str = "development"          # "production" refuses to start with a weak secret and hides /docs
    log_level: str = "INFO"
    trust_proxy: bool = False         # honour X-Forwarded-For (only behind your own reverse proxy)
    user_ai_per_min: int = 20         # AI actions one learner may start per minute (protects the shared team quota)
    login_attempts: int = 8           # failed sign-ins per 5 minutes, per connection and per pseudo
    register_per_hour: int = 15       # accounts created per connection per hour
    max_body_bytes: int = 4 * 1024 * 1024
    video_capacity: int = 6           # how many cameras may share one Live room at once (mesh WebRTC)

    @property
    def is_production(self) -> bool:
        return self.env == "production"

    def production_problems(self) -> list[str]:
        if not self.is_production:
            return []
        out = []
        if self.secret in ("dev-secret-change-me", "change-me-for-any-shared-deployment") or len(self.secret) < 24:
            out.append("EDNOVA_SECRET must be a random string of at least 24 characters")
        if not self.is_mock and self.api_key in ("", "replace-me", "dev-mock-key"):
            out.append("EDNOVA_API_KEY is not set")
        return out

    @property
    def is_mock(self) -> bool:
        return self.base_url.strip().lower() in ("mock", "inprocess")


def load_settings(**overrides) -> Settings:
    _load_dotenv()
    env = os.environ.get
    settings = Settings(
        base_url=env("EDNOVA_BASE_URL", "mock"),
        api_key=env("EDNOVA_API_KEY", "dev-mock-key"),
        compact_model=env("EDNOVA_COMPACT_MODEL", "compact-1b"),
        mid_model=env("EDNOVA_MID_MODEL", "mid-8b"),
        embed_model=env("EDNOVA_EMBED_MODEL", "embed-small"),
        min_similarity=float(env("EDNOVA_MIN_SIMILARITY", "0.1")),
        max_concurrent=int(env("EDNOVA_MAX_CONCURRENT", "2")),
        cache_ttl_s=int(env("EDNOVA_CACHE_TTL_S", str(6 * 3600))),
        max_quota_wait_s=float(env("EDNOVA_MAX_QUOTA_WAIT_S", "45")),
        db_path=env("EDNOVA_DB", "ednova.db"),
        secret=env("EDNOVA_SECRET", "dev-secret-change-me"),
        mock_latency_scale=float(env("EDNOVA_MOCK_LATENCY_SCALE", "1")),
        live_capacity=int(env("EDNOVA_LIVE_CAPACITY", "200")),
        env=env("EDNOVA_ENV", "development").strip().lower(),
        log_level=env("EDNOVA_LOG_LEVEL", "INFO"),
        trust_proxy=env("EDNOVA_TRUST_PROXY", "0") == "1",
        user_ai_per_min=int(env("EDNOVA_USER_AI_PER_MIN", "20")),
        login_attempts=int(env("EDNOVA_LOGIN_ATTEMPTS", "8")),
        register_per_hour=int(env("EDNOVA_REGISTER_PER_HOUR", "15")),
        video_capacity=int(env("EDNOVA_VIDEO_CAPACITY", "6")),
    )
    settings = dataclasses.replace(settings, **overrides) if overrides else settings
    problems = settings.production_problems()
    if problems:
        raise RuntimeError("Refusing to start in production: " + "; ".join(problems))
    return settings
