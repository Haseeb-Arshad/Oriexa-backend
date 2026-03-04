"""Compute agent reputation tiers from reputation_score, tasks_completed, avg_rating."""


def compute_reputation_tier(
    reputation_score: float,
    tasks_completed: int,
    avg_rating: float | None,
) -> dict:
    """Return tier name, label, and color info for an agent."""
    avg = avg_rating or 0.0

    if reputation_score >= 85 and tasks_completed >= 20 and avg >= 4.5:
        return {"tier": "elite", "label": "Elite", "color": "amber"}
    if reputation_score >= 70 and tasks_completed >= 10:
        return {"tier": "expert", "label": "Expert", "color": "sky"}
    if reputation_score >= 55 and tasks_completed >= 3:
        return {"tier": "proven", "label": "Proven", "color": "emerald"}
    return {"tier": "newcomer", "label": "Newcomer", "color": "stone"}


def enrich_agent_data(agent_row) -> dict:
    """Add reputation tier to an agent data dict from a DB row."""
    tier = compute_reputation_tier(
        getattr(agent_row, "reputation_score", 50.0),
        getattr(agent_row, "tasks_completed", 0),
        getattr(agent_row, "avg_rating", None),
    )
    return {
        "reputation_score": getattr(agent_row, "reputation_score", 50.0),
        "tasks_completed": getattr(agent_row, "tasks_completed", 0),
        "avg_rating": getattr(agent_row, "avg_rating", None),
        "reputation_tier": tier,
    }
