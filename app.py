from __future__ import annotations

import streamlit as st
from pydantic import ValidationError

from agent import SchedulingAgent
from calcom_client import CalcomAPIError
from models import AgentState


st.set_page_config(page_title="Cal.com Scheduling Agent", page_icon="📅")
st.title("📅 Cal.com Scheduling Agent")
st.caption("Book, list, cancel, and reschedule Cal.com events through plain conversation.")

if "messages" not in st.session_state:
    st.session_state.messages = [
        {
            "role": "assistant",
            "content": "Hi — ask me to book, list, cancel, or reschedule a Cal.com event.",
        }
    ]
if "agent_state" not in st.session_state:
    st.session_state.agent_state = AgentState().model_dump()


for message in st.session_state.messages:
    with st.chat_message(message["role"]):
        st.markdown(message["content"])

user_input = st.chat_input("Tell me what to do with your calendar...")
if user_input:
    st.session_state.messages.append({"role": "user", "content": user_input})
    with st.chat_message("user"):
        st.markdown(user_input)

    state = AgentState.model_validate(st.session_state.agent_state)
    agent = SchedulingAgent()
    try:
        answer, new_state = agent.handle_message(user_input, state, st.session_state.messages)
        st.session_state.agent_state = new_state.model_dump()
    except CalcomAPIError as exc:
        uid = state.awaiting_confirmation.booking_uid if state.awaiting_confirmation else ""
        answer = agent._format_calcom_error(exc, user_input, uid, state)
    except ValidationError as exc:
        answer = f"State validation failed: {exc}"
    except Exception as exc:
        answer = f"Unexpected error: {type(exc).__name__}: {exc}"

    st.session_state.messages.append({"role": "assistant", "content": answer})
    with st.chat_message("assistant"):
        st.markdown(answer)
