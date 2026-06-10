from __future__ import annotations

from dataclasses import dataclass
import os
from dotenv import load_dotenv

load_dotenv()


@dataclass(frozen=True)
class Settings:
    calcom_api_key: str | None = (os.getenv("CALCOM_API_KEY") or "").strip() or None
    calcom_event_type_id: int | None = (
        int(os.getenv("CALCOM_EVENT_TYPE_ID")) if os.getenv("CALCOM_EVENT_TYPE_ID") else None
    )
    calcom_event_type_slug: str | None = os.getenv("CALCOM_EVENT_TYPE_SLUG")
    calcom_username: str | None = os.getenv("CALCOM_USERNAME")
    openai_api_key: str | None = (os.getenv("OPENAI_API_KEY") or "").strip() or None
    openai_base_url: str | None = (os.getenv("OPENAI_BASE_URL") or "").strip() or None
    openai_model: str = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
    default_timezone: str = os.getenv("DEFAULT_TIMEZONE", "America/Los_Angeles")
    default_attendee_name: str = os.getenv("DEFAULT_ATTENDEE_NAME", "Demo User")
    default_attendee_email: str = os.getenv("DEFAULT_ATTENDEE_EMAIL", "demo@example.com")

    @property
    def has_calcom_auth(self) -> bool:
        return bool(self.calcom_api_key)

    @property
    def has_event_type_config(self) -> bool:
        return bool(self.calcom_event_type_id or (self.calcom_event_type_slug and self.calcom_username))


settings = Settings()
