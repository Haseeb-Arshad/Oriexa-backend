"""
Node: analyze_content

Uses the resolved LLM to evaluate the deliverable against the task requirements.
Returns structured scores and a PASS/FAIL recommendation.
"""

from __future__ import annotations
import json
import os
from app.agents.user_reviewer.state import ReviewerState

# LangChain LLM wrappers (imported lazily to avoid import errors if not installed)
def _get_llm(provider: str, api_key: str, model: str):
    """Create a LangChain LLM instance for the given provider."""
    if provider == "openrouter":
        from langchain_openai import ChatOpenAI
        return ChatOpenAI(
            model=model,
            api_key=api_key,
            base_url="https://openrouter.ai/api/v1",
            default_headers={
                "HTTP-Referer": os.environ.get("ORIEXA_BASE_URL", "https://oriexa.vercel.app"),
                "X-Title": "Oriexa Reviewer Agent",
            },
        )
    elif provider == "anthropic":
        from langchain_anthropic import ChatAnthropic
        return ChatAnthropic(model=model, api_key=api_key)  # type: ignore[arg-type]
    elif provider == "openai":
        from langchain_openai import ChatOpenAI
        return ChatOpenAI(model=model, api_key=api_key)
    else:
        raise ValueError(f"Unknown LLM provider: {provider}")


REVIEW_PROMPT = """\
You are an expert deliverable reviewer for the Oriexa freelancer marketplace.

## Task Details

**Title:** {title}

**Description:**
{description}

**Requirements:**
{requirements}

## Submitted Deliverable

{content}

---

## Your Task

Evaluate whether the submitted deliverable fully satisfies the task requirements.
Your verdict must be strictly binary: PASS or FAIL.

**PASS** = ALL requirements are completely and correctly met.
**FAIL** = Any requirement is missing, incomplete, or incorrect.

Even if 90% of requirements are met, it is a FAIL. Be strict and objective.

Respond ONLY with a valid JSON object in this exact format:
{{
  "verdict": "pass" or "fail",
  "requirements_met": <integer 0-10, how many requirements are fully met>,
  "requirements_total": <integer, total number of distinct requirements>,
  "quality_score": <integer 0-10, overall quality of the work>,
  "completeness_score": <integer 0-10, how complete the deliverable is>,
  "correctness_score": <integer 0-10, technical correctness>,
  "feedback": "<2-3 sentences explaining the verdict. If FAIL, specify exactly what is missing or wrong. If PASS, confirm what was done well.>"
}}

Do not include any text outside the JSON object.
"""


def analyze_content(state: ReviewerState) -> dict:
    """Use LLM to evaluate the deliverable against task requirements."""
    if state.get("skip_review") or state.get("error"):
        return {}

    llm_api_key = state.get("llm_api_key")
    llm_provider = state.get("llm_provider", "anthropic")
    llm_model = state.get("llm_model", "claude-sonnet-4-6")

    if not llm_api_key:
        return {"skip_review": True}

    requirements_text = state.get("task_requirements") or "No specific requirements listed — use the description."

    prompt = REVIEW_PROMPT.format(
        title=state.get("task_title", "Unknown"),
        description=state.get("task_description", ""),
        requirements=requirements_text,
        content=state.get("deliverable_content", ""),
    )

    try:
        llm = _get_llm(llm_provider, llm_api_key, llm_model)
        from langchain_core.messages import HumanMessage
        response = llm.invoke([HumanMessage(content=prompt)])
        raw = response.content if hasattr(response, "content") else str(response)
    except Exception as exc:
        return {"error": f"LLM call failed: {exc}"}

    # Parse JSON response
    try:
        # Strip any markdown code fences if present
        cleaned = raw.strip()
        if cleaned.startswith("```"):
            lines = cleaned.split("\n")
            cleaned = "\n".join(lines[1:-1])
        result = json.loads(cleaned)
    except json.JSONDecodeError:
        # If JSON parsing fails, try to extract verdict from text
        lower = raw.lower()
        if "pass" in lower and "fail" not in lower:
            result = {
                "verdict": "pass",
                "feedback": raw[:500],
                "quality_score": 7,
                "completeness_score": 8,
                "correctness_score": 7,
                "requirements_met": 1,
                "requirements_total": 1,
            }
        else:
            result = {
                "verdict": "fail",
                "feedback": f"Could not parse LLM response. Raw response: {raw[:300]}",
                "quality_score": 0,
                "completeness_score": 0,
                "correctness_score": 0,
                "requirements_met": 0,
                "requirements_total": 1,
            }

    verdict = result.get("verdict", "fail").lower()
    if verdict not in ("pass", "fail"):
        verdict = "fail"

    scores = {
        "requirements_met": result.get("requirements_met", 0),
        "requirements_total": result.get("requirements_total", 1),
        "quality_score": result.get("quality_score", 0),
        "completeness_score": result.get("completeness_score", 0),
        "correctness_score": result.get("correctness_score", 0),
    }

    return {
        "verdict": verdict,
        "review_feedback": result.get("feedback", ""),
        "review_scores": scores,
        "llm_model": llm_model,
    }
