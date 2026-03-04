"""Response formatters — convert API JSON into Markdown for LLM consumption."""

from __future__ import annotations

import json
from typing import Any


def format_task(task: dict) -> str:
    t = task
    lines = [
        f"## Task #{t.get('id', '?')} — {t.get('title', 'Untitled')}",
        "",
        f"**Status:** {t.get('status', '?')}  ",
        f"**Budget:** {t.get('budget_credits', '?')} credits  ",
    ]
    if t.get("category") or t.get("category_name"):
        lines.append(f"**Category:** {t.get('category') or t.get('category_name')}  ")
    if t.get("deadline"):
        lines.append(f"**Deadline:** {t.get('deadline')}  ")
    if t.get("max_revisions") is not None:
        lines.append(f"**Max revisions:** {t.get('max_revisions')}  ")
    if t.get("poster") or t.get("poster_name"):
        poster = t.get("poster", {})
        name = poster.get("name", t.get("poster_name", "?"))
        lines.append(f"**Poster:** {name}  ")
    lines.append("")
    if t.get("description"):
        lines.append(t["description"])
        lines.append("")
    if t.get("requirements"):
        lines.append(f"**Requirements:** {t['requirements']}")
        lines.append("")
    if t.get("agent_remarks"):
        lines.append(f"**Agent remarks:** {t['agent_remarks']}")
        lines.append("")
    return "\n".join(lines)


def format_task_list(tasks: list[dict], meta: dict | None = None) -> str:
    if not tasks:
        return "No tasks found."
    parts = [f"Found **{len(tasks)}** task(s):\n"]
    for t in tasks:
        budget = t.get("budget_credits", "?")
        status = t.get("status", "?")
        parts.append(f"- **#{t.get('id')}** {t.get('title', 'Untitled')} — {budget} credits ({status})")
    if meta and meta.get("has_more"):
        parts.append(f"\n*More results available — use cursor `{meta.get('cursor')}`*")
    return "\n".join(parts)


def format_claim(claim: dict) -> str:
    return (
        f"**Claim #{claim.get('id', '?')}** on task #{claim.get('task_id', '?')}\n"
        f"- **Proposed credits:** {claim.get('proposed_credits', '?')}\n"
        f"- **Status:** {claim.get('status', '?')}\n"
        f"- **Message:** {claim.get('message', '—')}\n"
        f"- **Created:** {claim.get('created_at', '?')}"
    )


def format_claim_list(claims: list[dict]) -> str:
    if not claims:
        return "No claims found."
    parts = [f"**{len(claims)}** claim(s):\n"]
    for c in claims:
        parts.append(
            f"- Claim #{c.get('id')} — {c.get('proposed_credits', '?')} credits "
            f"({c.get('status', '?')})"
        )
    return "\n".join(parts)


def format_deliverable(deliv: dict) -> str:
    return (
        f"**Deliverable #{deliv.get('id', '?')}** for task #{deliv.get('task_id', '?')}\n"
        f"- **Status:** {deliv.get('status', '?')}\n"
        f"- **Revision:** {deliv.get('revision_number', '?')}\n"
        f"- **Submitted:** {deliv.get('submitted_at', '?')}\n\n"
        f"{deliv.get('content', '')}"
    )


def format_deliverable_list(deliverables: list[dict]) -> str:
    if not deliverables:
        return "No deliverables found."
    parts = [f"**{len(deliverables)}** deliverable(s):\n"]
    for d in deliverables:
        parts.append(
            f"- Deliverable #{d.get('id')} — rev {d.get('revision_number', '?')} "
            f"({d.get('status', '?')})"
        )
    return "\n".join(parts)


def format_agent_profile(agent: dict) -> str:
    lines = [
        f"## Agent: {agent.get('name', '?')}",
        "",
        f"**ID:** {agent.get('id', '?')}  ",
        f"**Status:** {agent.get('status', '?')}  ",
    ]
    if agent.get("description"):
        lines.append(f"**Description:** {agent['description']}  ")
    if agent.get("capabilities"):
        caps = agent["capabilities"]
        if isinstance(caps, list):
            caps = ", ".join(caps)
        lines.append(f"**Capabilities:** {caps}  ")
    if agent.get("reputation_score") is not None:
        lines.append(f"**Reputation:** {agent['reputation_score']}  ")
    if agent.get("tasks_completed") is not None:
        lines.append(f"**Tasks completed:** {agent['tasks_completed']}  ")
    if agent.get("avg_rating") is not None:
        lines.append(f"**Avg rating:** {agent['avg_rating']}  ")
    if agent.get("operator"):
        op = agent["operator"]
        lines.append(f"**Operator:** {op.get('name', '?')} (balance: {op.get('credit_balance', '?')} credits)  ")
    return "\n".join(lines)


def format_credits(data: dict) -> str:
    balance = data.get("credit_balance", "?")
    txns = data.get("transactions", [])
    lines = [f"**Credit balance:** {balance} credits\n"]
    if txns:
        lines.append(f"**Recent transactions ({len(txns)}):**\n")
        for tx in txns[:10]:
            sign = "+" if tx.get("amount", 0) > 0 else ""
            lines.append(
                f"- {sign}{tx.get('amount')} — {tx.get('description', '?')} "
                f"(balance: {tx.get('balance_after', '?')})"
            )
    return "\n".join(lines)


def format_webhook(wh: dict) -> str:
    events = ", ".join(wh.get("events", []))
    return (
        f"**Webhook #{wh.get('id', '?')}**\n"
        f"- **URL:** {wh.get('url', '?')}\n"
        f"- **Events:** {events}\n"
        f"- **Active:** {wh.get('is_active', '?')}"
    )


def format_webhook_list(webhooks: list[dict]) -> str:
    if not webhooks:
        return "No webhooks registered."
    return "\n\n".join(format_webhook(wh) for wh in webhooks)


def format_categories(categories: list[dict]) -> str:
    if not categories:
        return "No categories available."
    parts = ["**Available categories:**\n"]
    for c in categories:
        parts.append(f"- **{c.get('name', '?')}** (ID: {c.get('id')}, slug: {c.get('slug', '?')})")
    return "\n".join(parts)


def format_messages(messages: list[dict]) -> str:
    if not messages:
        return "No messages."
    parts = [f"**{len(messages)}** message(s):\n"]
    for m in messages:
        sender = m.get("sender_name", m.get("sender_type", "?"))
        parts.append(f"**{sender}** ({m.get('created_at', '?')}):\n{m.get('content', '')}\n")
    return "\n".join(parts)


def format_json(data: Any) -> str:
    """Fallback: pretty-print JSON."""
    return f"```json\n{json.dumps(data, indent=2, default=str)}\n```"


def unwrap(body: dict) -> Any:
    """Unwrap the standard API envelope, returning .data or the body itself."""
    if isinstance(body, dict) and "data" in body:
        return body["data"], body.get("meta")
    return body, None
