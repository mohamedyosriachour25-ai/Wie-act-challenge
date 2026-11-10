"""Runtime settings. Everything that differs between dev and the challenge
(base URL, key, model names) comes from the environment: nothing is hard-coded.

There is no built-in fake AI service and no fallback: HERWIEGO_BASE_URL and HERWIEGO_API_KEY must point
at a real OpenAI-compatible inference service, in every environment, or the app refuses to start."""
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
    # Your real OpenAI-compatible inference service. No default works out of the box on purpose:
    # there is nothing for the app to fall back to.
    base_url: str = ""
    api_key: str = ""
    compact_model: str = "compact-1b"
    mid_model: str = "mid-8b"
    embed_model: str = "embed-small"
    min_similarity: float = 0.1       # below this a passage is not considered relevant (tune for the real embeddings)
    max_concurrent: int = 2           # the service caps concurrent requests per key
    cache_ttl_s: int = 6 * 3600
    semantic_cache: bool = True        # reuse a result for a near-duplicate request (embeddings-based)
    sem_threshold: float = 0.86        # cosine above which an explanation is reused
    sem_threshold_ask: float = 0.93    # stricter for material questions: a small wording change can change intent
    quiz_verify: bool = True           # draft-then-revise: a compact pass reviews the mid-size quiz draft
    max_quota_wait_s: float = 45.0    # longer than this and we tell the user instead of blocking
    db_path: str = "herwiego.db"
    secret: str = "dev-secret-change-me"
    live_capacity: int = 500          # the public space (presence + chat) holds many; cameras are capped below
    env: str = "development"          # "production" refuses to start with a weak secret and hides /docs
    log_level: str = "INFO"
    trust_proxy: bool = False         # honour X-Forwarded-For (only behind your own reverse proxy)
    user_ai_per_min: int = 20         # AI actions one learner may start per minute (protects the shared team quota)
    login_attempts: int = 8           # failed sign-ins per 5 minutes, per connection and per pseudo
    register_per_hour: int = 15       # accounts created per connection per hour
    max_body_bytes: int = 4 * 1024 * 1024
    video_capacity: int = 9           # cameras sharing one room at once (peer-to-peer mesh; an SFU would lift this)
    event_lead_s: int = 300           # how long before a break the group-game invite opens (clamped to the focus length)
    event_min_players: int = 2        # a group game needs at least this many people who opted in

    @property
    def is_production(self) -> bool:
        return self.env == "production"

    def startup_problems(self) -> list[str]:
        """Checked in every environment: there is no fake AI service to fall back to."""
        out = []
        if self.is_production and (self.secret in ("dev-secret-change-me", "change-me-for-any-shared-deployment") or len(self.secret) < 24):
            out.append("HERWIEGO_SECRET must be a random string of at least 24 characters")
        if not self.base_url.strip():
            out.append("HERWIEGO_BASE_URL is not set: point it at your real AI service (there is no mock fallback)")
        if self.api_key in ("", "replace-me"):
            out.append("HERWIEGO_API_KEY is not set")
        return out


def load_settings(**overrides) -> Settings:
    _load_dotenv()
    env = os.environ.get
    settings = Settings(
        base_url=env("HERWIEGO_BASE_URL", ""),
        api_key=env("HERWIEGO_API_KEY", ""),
        compact_model=env("HERWIEGO_COMPACT_MODEL", "compact-1b"),
        mid_model=env("HERWIEGO_MID_MODEL", "mid-8b"),
        embed_model=env("HERWIEGO_EMBED_MODEL", "embed-small"),
        min_similarity=float(env("HERWIEGO_MIN_SIMILARITY", "0.1")),
        max_concurrent=int(env("HERWIEGO_MAX_CONCURRENT", "2")),
        cache_ttl_s=int(env("HERWIEGO_CACHE_TTL_S", str(6 * 3600))),
        semantic_cache=env("HERWIEGO_SEMANTIC_CACHE", "1") != "0",
        sem_threshold=float(env("HERWIEGO_SEM_THRESHOLD", "0.86")),
        sem_threshold_ask=float(env("HERWIEGO_SEM_THRESHOLD_ASK", "0.93")),
        quiz_verify=env("HERWIEGO_QUIZ_VERIFY", "1") != "0",
        max_quota_wait_s=float(env("HERWIEGO_MAX_QUOTA_WAIT_S", "45")),
        db_path=env("HERWIEGO_DB", "herwiego.db"),
        secret=env("HERWIEGO_SECRET", "dev-secret-change-me"),
        live_capacity=int(env("HERWIEGO_LIVE_CAPACITY", "200")),
        env=env("HERWIEGO_ENV", "development").strip().lower(),
        log_level=env("HERWIEGO_LOG_LEVEL", "INFO"),
        trust_proxy=env("HERWIEGO_TRUST_PROXY", "0") == "1",
        user_ai_per_min=int(env("HERWIEGO_USER_AI_PER_MIN", "20")),
        login_attempts=int(env("HERWIEGO_LOGIN_ATTEMPTS", "8")),
        register_per_hour=int(env("HERWIEGO_REGISTER_PER_HOUR", "15")),
        video_capacity=int(env("HERWIEGO_VIDEO_CAPACITY", "9")),
        event_lead_s=int(env("HERWIEGO_EVENT_LEAD_S", "300")),
        event_min_players=int(env("HERWIEGO_EVENT_MIN_PLAYERS", "2")),
    )
    settings = dataclasses.replace(settings, **overrides) if overrides else settings
    problems = settings.startup_problems()
    if problems:
        raise RuntimeError("Refusing to start: " + "; ".join(problems))
    return settings
