"""Quick check for OpenAI / compatible API connectivity."""

from __future__ import annotations

import os
import sys

import httpx
from dotenv import load_dotenv

load_dotenv()


def main() -> int:
    api_key = (os.getenv("OPENAI_API_KEY") or "").strip()
    base_url = (os.getenv("OPENAI_BASE_URL") or "https://api.openai.com/v1").strip().rstrip("/")
    model = os.getenv("OPENAI_MODEL", "gpt-4o-mini")

    if not api_key:
        print("OPENAI_API_KEY is not set. Natural-language parsing will not work.")
        return 1

    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    print(f"Base URL: {base_url}")
    print(f"Model: {model}")

    with httpx.Client(timeout=30) as client:
        models = client.get(f"{base_url}/models", headers=headers)
        print(f"GET /models -> {models.status_code}")
        if models.status_code != 200:
            print(models.text[:500])
            return 2

        response = client.post(
            f"{base_url}/chat/completions",
            headers=headers,
            json={
                "model": model,
                "messages": [{"role": "user", "content": "Reply with exactly: OK"}],
                "temperature": 0,
                "max_tokens": 10,
            },
        )
        print(f"POST /chat/completions -> {response.status_code}")
        if response.status_code != 200:
            print(response.text[:500])
            return 3

        content = response.json()["choices"][0]["message"]["content"]
        print(f"LLM reply: {content!r}")
        print("OpenAI-compatible API is working.")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
