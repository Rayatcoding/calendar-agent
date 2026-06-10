# Cal.com Scheduling Assistant

A conversational assistant for [Cal.com](https://cal.com). Chat in plain English to **book**, **list**, **cancel**, and **reschedule** meetings. Built for the Cal.com take-home challenge ([`Calcom-README.md`](Calcom-README.md)).

**Stack:** Streamlit UI · OpenAI intent parsing · deterministic Cal.com API execution · friendly response formatting

---

## Quick start

### 1. Clone and install

```bash
git clone <your-repo-url>
cd calcom_assistant
python -m venv .venv
```

**Windows (PowerShell):**

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

**macOS / Linux:**

```bash
source .venv/bin/activate
pip install -r requirements.txt
```

### 2. Configure environment

We **do not** commit your real `.env` to GitHub. The repo includes **`.env.example`** (empty placeholders) — copy it locally and fill in your keys:

**Windows (PowerShell):**

```powershell
copy .env.example .env
```

**macOS / Linux:**

```bash
cp .env.example .env
```

Then edit **`.env`** with your API keys (see [Configuration](#configuration) below). Only `.env.example` is uploaded; your filled-in `.env` stays on your machine.

### 3. Run the app

**Windows:**

```powershell
.\.venv\Scripts\python.exe -m streamlit run app.py
```

**macOS / Linux:**

```bash
streamlit run app.py
```

Open the URL shown in the terminal (usually `http://localhost:8501`).

---

## Configuration

| Variable | Required | Description |
|----------|----------|-------------|
| `CALCOM_API_KEY` | Yes | Cal.com API key ([docs](https://cal.com/docs/enterprise-features/api/authentication)) |
| `CALCOM_EVENT_TYPE_ID` | Yes* | Numeric ID of the event type to book |
| `CALCOM_EVENT_TYPE_SLUG` | Alt* | Event slug (use with `CALCOM_USERNAME` instead of ID) |
| `CALCOM_USERNAME` | Alt* | Your Cal.com username |
| `OPENAI_API_KEY` | Yes | OpenAI API key for natural-language parsing |
| `OPENAI_MODEL` | No | Default: `gpt-4o-mini` |
| `OPENAI_BASE_URL` | No | Leave empty for OpenAI. Set only for compatible proxies. |
| `DEFAULT_TIMEZONE` | No | Default: `America/Los_Angeles` |
| `DEFAULT_ATTENDEE_NAME` | No | Default guest name when booking without one |
| `DEFAULT_ATTENDEE_EMAIL` | No | Default guest email when booking without one |

\* Provide either `CALCOM_EVENT_TYPE_ID` **or** both `CALCOM_EVENT_TYPE_SLUG` + `CALCOM_USERNAME`.

**Check OpenAI connectivity:**

```bash
python scripts/check_openai_connection.py
```

---

## What you can ask

| Action | Example |
|--------|---------|
| List meetings | `show my meetings` · `what's on my calendar tomorrow?` |
| Book | `book` → assistant asks for details · `book June 12 at 2pm` |
| Cancel | `cancel my 3pm meeting on June 12` · `cancel it` (after a recent booking) |
| Reschedule | `reschedule my 10am meeting to 3pm` · `move it to Friday 2pm` |
| Slots | `what slots are available on June 15?` |

The assistant will:

- Ask for **missing details** (time, attendee, etc.) before booking
- Show **available Cal.com slots** when a time isn't open
- Ask **yes / no** before cancel or reschedule
- Use **local-friendly times** in replies (not raw UTC)

---

## Tests

```bash
# Unit tests (no live API calls)
pytest -q

# Optional: live Cal.com smoke tests
set CALCOM_RUN_LIVE_TESTS=1        # Windows cmd
$env:CALCOM_RUN_LIVE_TESTS='1'    # PowerShell
pytest tests/test_integration_live.py -q
```

---

## How it works

```
User message
  → LLM parser (intent + fields + session context)
  → Scheduling agent (validation, confirmations, slot matching)
  → Cal.com API (list / slots / book / cancel / reschedule)
  → Response formatter (readable reply)
```

**Design choices:**

- **LLM parses language; Python executes actions** — the model never mutates the calendar directly.
- **Cal.com is the source of truth** — bookings only use slots returned by the API.
- **Small deterministic helpers** — confirmations, slot selection by number/label, and safety checks (e.g. can't reschedule a cancelled meeting).
- **Session context** — recent bookings and pending commands are passed to the parser for follow-ups like `cancel it`.

---

## Project layout

```
app.py                      # Streamlit chat UI (entry point)
agent.py                    # Workflow orchestration
llm_router.py               # LLM intent parser
response_formatter.py       # User-facing replies
calcom_client.py            # Cal.com API v2 client
booking_resolver.py         # Match meetings by time / UID
parse_utils.py              # Follow-up parsing helpers
models.py                   # Pydantic models
prompts.py                  # LLM system prompts
config.py                   # Settings from .env
tests/                      # Pytest suite (include in repo)
scripts/                    # Optional dev utilities
Calcom-README.md            # Original challenge prompt
```

---

## Cal.com API references

- [Authentication](https://cal.com/docs/enterprise-features/api/authentication)
- [Bookings API](https://cal.com/docs/api-reference/v2/bookings/get-all-bookings)
- [Slots API](https://cal.com/docs/api-reference/v2/slots/find-out-when-is-an-event-type-ready-to-be-booked)

---

## Notes for reviewers

- **`.env.example`** is on GitHub (template). **`.env`** is gitignored (your secrets).
- Copy `.env.example` → `.env`, then add your Cal.com and OpenAI keys.
- `OPENAI_API_KEY` is required for natural-language understanding.
- Original challenge wording is in [`Calcom-README.md`](Calcom-README.md).
