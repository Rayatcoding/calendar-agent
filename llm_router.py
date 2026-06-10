from __future__ import annotations

import json
from datetime import datetime
from zoneinfo import ZoneInfo

from pydantic import ValidationError

from config import Settings, settings
from models import AgentState, Intent, ParsedCommand
from prompts import AGENT_SYSTEM_PROMPT

MISSING_OPENAI_KEY = "missing_openai_api_key"
PARSE_ERROR_PREFIX = "parse_error:"


class CommandParser:
    """Converts natural language into a typed command via LLM JSON parsing."""

    def __init__(self, cfg: Settings = settings):
        self.cfg = cfg
        self.client = None
        if cfg.openai_api_key:
            from openai import OpenAI

            # Always pass an explicit base URL. An empty OPENAI_BASE_URL in .env
            # otherwise leaks into the SDK as "" and breaks every request.
            client_kwargs: dict[str, str] = {
                "api_key": cfg.openai_api_key,
                "base_url": cfg.openai_base_url or "https://api.openai.com/v1",
            }
            self.client = OpenAI(**client_kwargs)

    def parse(
        self,
        text: str,
        history: list[dict] | None = None,
        state: AgentState | None = None,
    ) -> ParsedCommand:
        if not self.client:
            return ParsedCommand(intent=Intent.UNKNOWN, summary=MISSING_OPENAI_KEY)
        try:
            return self._parse_with_llm(text, history or [], state)
        except Exception as exc:
            return ParsedCommand(
                intent=Intent.UNKNOWN,
                summary=f"{PARSE_ERROR_PREFIX} {type(exc).__name__}",
            )

    def _parse_with_llm(
        self,
        text: str,
        history: list[dict],
        state: AgentState | None,
    ) -> ParsedCommand:
        now_local = datetime.now(ZoneInfo(self.cfg.default_timezone)).isoformat()
        context = {
            "timezone": self.cfg.default_timezone,
            "current_local_datetime": now_local,
            "recent_chat_history": history[-8:],
            "pending_command": state.pending_command.model_dump() if state and state.pending_command else None,
            "recent_bookings": [
                booking.model_dump()
                for booking in (state.recent_bookings if state else [])
            ],
            "candidate_slots": [
                slot.model_dump()
                for slot in (state.candidate_slots if state else [])
            ],
            "user_message": text,
        }
        messages = [
            {"role": "system", "content": AGENT_SYSTEM_PROMPT},
            {"role": "user", "content": json.dumps(context)},
        ]
        response = self.client.chat.completions.create(
            model=self.cfg.openai_model,
            messages=messages,
            temperature=0,
            response_format={"type": "json_object"},
        )
        payload = json.loads(response.choices[0].message.content or "{}")
        try:
            return ParsedCommand.model_validate(payload)
        except ValidationError:
            return ParsedCommand(intent=Intent.UNKNOWN, summary="invalid_llm_response")
