#!/usr/bin/env python3
"""
Oriexa Scout Agent — Browse, Evaluate, and Claim Tasks

One-shot agent that:
  1. Browses open tasks
  2. Evaluates them via LLM
  3. Posts constructive feedback on vague tasks
  4. Claims the best matching task

Usage (called by orchestrator, not directly):
    python -m agents.scout_agent --api-key <key> [--base-url <url>]
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import traceback
from datetime import datetime, timezone

# Add parent to path so we can import base_agent
sys.path.insert(0, str(__import__("pathlib").Path(__file__).parent.parent))

from agents.base_agent import (
    BASE_URL,
    OriexaClient,
    iso_to_datetime,
    llm_json,
    log_act,
    log_err,
    log_ok,
    log_think,
    log_wait,
    log_warn,
)

AGENT_NAME = "Scout"
MAX_REMARKS_PER_TASK = 4  # initial + follow-ups after poster answers
SCOUT_BROWSE_LIMIT = int(os.environ.get("SCOUT_BROWSE_LIMIT", "8"))


# ═══════════════════════════════════════════════════════════════════════════
# SCOUT BRAIN
# ═══════════════════════════════════════════════════════════════════════════

def evaluate_task(task: dict, capabilities: list[str], conv_messages: list[dict] | None = None) -> dict:
    """THINK: Should I claim this task? How much to bid?"""
    remarks = task.get("agent_remarks", [])
    remarks_history = ""
    has_answered_questions = False
    if remarks:
        remarks_history = "\nPrevious agent feedback on this task:\n"
        for r in remarks:
            remarks_history += f"- {r.get('agent_name')}: {r.get('remark')}\n"
            eval_data = r.get("evaluation")
            if eval_data:
                for q in eval_data.get("questions", []):
                    if q.get("answer"):
                        has_answered_questions = True
                        remarks_history += f"  Q: {q['text']} -> Poster answered: {q['answer']}\n"
                    else:
                        remarks_history += f"  Q: {q['text']} -> (not answered yet)\n"

    # Include poster's free-form text messages so the LLM sees direct replies
    if conv_messages:
        poster_msgs = [
            m for m in conv_messages
            if m.get("sender_type") == "poster" and m.get("message_type") == "text"
        ]
        if poster_msgs:
            has_answered_questions = True
            remarks_history += "\nPoster messages in chat:\n"
            for m in poster_msgs[-5:]:
                remarks_history += f"  Poster: {m.get('content', '')}\n"

    # Build system prompt — different for follow-up vs initial evaluation
    if has_answered_questions:
        system_prompt = (
            "You are an AI freelancer agent on Oriexa. The task poster has answered your evaluation questions. "
            f"Your capabilities: {', '.join(capabilities)}. "
            "Review their answers carefully.\n\n"
            "STACK RESTRICTION: You ONLY work on frontend/web projects using Next.js, React, or Vite. "
            "If the task is NOT a frontend/web project, set should_claim=false.\n\n"
            "FOLLOW-UP RULES:\n"
            "- The poster has answered your questions. You now have enough info to start.\n"
            "- Set should_claim=true and confidence=high (mandatory).\n"
            "- Set feedback to 1 sentence only: 'Got it — ready to start.'\n"
            "- questions: [] (empty — do NOT ask more questions)\n"
            "- approach: a concrete step-by-step plan based on the answers\n"
        )
    else:
        system_prompt = (
            "You are an AI freelancer agent on Oriexa evaluating tasks to decide which to claim. "
            f"Your capabilities: {', '.join(capabilities)}. "
            "Be selective but pragmatic — claim tasks you can deliver quality work for. "
            "If a task has a clear title, reasonable description, and any stated requirements, that is usually ENOUGH to claim. "
            "Do NOT reject tasks for being 'too simple' or 'too small'. "
            "Only flag a task as vague if the description is truly insufficient to start work.\n\n"
            "STACK RESTRICTION: You ONLY work on frontend/web projects using Next.js, React, or Vite. "
            "If the task is NOT a frontend/web project (e.g. Python scripts, mobile apps, hardware), set should_claim=false.\n\n"
            "IMPORTANT: On this initial evaluation round, set should_claim=false. "
            "You are gathering requirements first. You will claim AFTER the poster answers your questions.\n\n"
            "FEEDBACK FIELD RULES (CRITICAL):\n"
            "- The 'feedback' field is shown directly to the poster BEFORE your questions appear\n"
            "- Keep it to ONE sentence that states your intent: e.g. 'I can build this — a few quick questions:'\n"
            "- NO chatty greetings, no 'Hey!', no 'I took a look at', no preambles\n"
            "- NO restating the task title back to the poster\n"
            "- Just say what you need and why, then the questions will follow\n\n"
            "EVALUATION QUESTIONS — focus here. You have 4 question types:\n"
            "  1. 'multiple_choice' — For concrete decisions (tech stack, feature priorities, design style). "
            "Include 3-4 specific options.\n"
            "  2. 'yes_no' — For quick feature toggles (Need auth? Want dark mode? Mobile responsive?).\n"
            "  3. 'text_input' — For open-ended details (use case, target audience, specific behavior). "
            "Include a helpful placeholder.\n"
            "  4. 'scale' — For priorities on a spectrum (Polish level? Complexity? Performance vs features?). "
            "Use scale_min, scale_max (1-5), and scale_labels for endpoints.\n\n"
            "Use 3-5 questions total. Mix types. Be specific, never generic.\n"
            "NEVER ask questions the description already answers.\n"
            "NEVER ask vague questions like 'What is the expected output?' — be concrete."
        )

    result = llm_json(
        system_prompt,

        f"Evaluate this task:\n"
        f"  Title: {task.get('title', 'N/A')}\n"
        f"  Description: {(task.get('description') or 'N/A')[:800]}\n"
        f"  Budget: {task.get('budget_credits', 0)} credits\n"
        f"  Requirements: {(task.get('requirements') or 'N/A')[:500]}\n"
        f"{remarks_history}\n"
        f"  Category: {task.get('category', {}).get('name', 'General') if isinstance(task.get('category'), dict) else 'General'}\n\n"
        + (
            # Follow-up prompt: poster answered, claim now
            "The poster has answered your questions. Respond with valid JSON:\n"
            '{\n'
            '  "should_claim": true,\n'
            '  "confidence": "high",\n'
            '  "proposed_credits": 100,\n'
            '  "is_vague": false,\n'
            '  "evaluation": {"score": 9, "strengths": ["Poster provided clear answers"], "concerns": [], "questions": []},\n'
            '  "feedback": "Got it — starting on this right away.",\n'
            '  "reason": "Poster answered all questions",\n'
            '  "approach": "Concrete step-by-step plan based on the answers provided."\n'
            '}\n'
            "Replace 100 with your actual proposed_credits."
            if has_answered_questions else
            # Initial prompt: ask questions, do NOT claim
            "IMPORTANT: Set should_claim to FALSE. You are gathering requirements first.\n\n"
            "Respond with this JSON structure (use valid JSON only):\n"
            '{\n'
            '  "should_claim": false,\n'
            '  "confidence": "medium",\n'
            '  "proposed_credits": 50,\n'
            '  "is_vague": false,\n'
            '  "evaluation": {\n'
            '    "score": 7,\n'
            '    "strengths": ["strength1", "strength2"],\n'
            '    "concerns": ["concern1"],\n'
            '    "questions": [\n'
            '      {"id": "q1", "text": "...", "type": "yes_no"},\n'
            '      {"id": "q2", "text": "...", "type": "multiple_choice", "options": ["A", "B", "C"]}\n'
            '    ]\n'
            '  },\n'
            '  "feedback": "ONE sentence stating intent, e.g. \'I can handle this — just need a few details:\' NO greetings, NO restating the task title",\n'
            '  "reason": "internal reason",\n'
            '  "approach": "step-by-step plan"\n'
            '}'
        ),
        max_tokens=2048,
        complexity="routine"
    )

    # Hard override based on phase
    if has_answered_questions:
        result["should_claim"] = True
        result["confidence"] = "high"
        if not result.get("feedback"):
            result["feedback"] = "Got it — starting on this right away."
        eval_obj = result.get("evaluation")
        if isinstance(eval_obj, dict):
            eval_obj["questions"] = []
    else:
        # Initial: NEVER claim — feedback only
        result["should_claim"] = False
        eval_obj = result.get("evaluation")
        if isinstance(eval_obj, dict) and not eval_obj.get("questions"):
            eval_obj["questions"] = [
                {"id": "q1", "text": "Do you need user accounts and login?", "type": "yes_no"},
                {"id": "q2", "text": "What vibe are you going for with the design?", "type": "multiple_choice",
                 "options": ["Minimal & clean", "Bold & colorful", "Dark & sleek", "No preference"]},
                {"id": "q3", "text": "Describe what the main page should look like", "type": "text_input",
                 "placeholder": "e.g. A list of items with a search bar..."},
                {"id": "q4", "text": "How polished should the final product be?", "type": "scale",
                 "scale_min": 1, "scale_max": 5, "scale_labels": ["Quick prototype", "Production-ready"]},
            ]

    # Ensure evaluation exists — generate context-aware fallback if LLM omitted it
    if not result.get("evaluation") or not isinstance(result.get("evaluation"), dict):
        title = task.get("title", "this task")
        desc = (task.get("description") or "")[:200]
        budget = task.get("budget_credits", 0)

        strengths = []
        if title and title != "N/A":
            strengths.append("Clear title that communicates the goal well")
        if budget >= 50:
            strengths.append(f"Reasonable budget of {budget} credits for this scope")
        if desc and len(desc) > 50:
            strengths.append("Solid starting description to work from")

        concerns = []
        reqs = task.get("requirements") or ""
        if not reqs:
            concerns.append("No acceptance criteria yet — a few quick answers below will fix that")
        if len(desc) < 100:
            concerns.append("A bit more detail would help agents nail the deliverable on the first try")

        result["evaluation"] = {
            "score": 7 if has_answered_questions else (6 if len(desc) > 80 else 4),
            "strengths": (
                ["Poster provided great answers to clarify the project", "Clear direction for implementation"]
                if has_answered_questions
                else strengths or ["The core concept is clear and achievable"]
            ),
            "concerns": (
                []
                if has_answered_questions
                else concerns or ["A few quick clarifications would go a long way"]
            ),
            "questions": [] if has_answered_questions else [
                {
                    "id": "q1",
                    "text": "Do you need user accounts and login?",
                    "type": "yes_no",
                },
                {
                    "id": "q2",
                    "text": "What vibe are you going for with the design?",
                    "type": "multiple_choice",
                    "options": ["Minimal & clean", "Bold & colorful", "Dark & sleek", "No preference — surprise me"],
                },
                {
                    "id": "q3",
                    "text": "Describe what the main page should look like in your own words",
                    "type": "text_input",
                    "placeholder": "e.g. A list of items with a search bar, each item shows a thumbnail and rating stars...",
                },
                {
                    "id": "q4",
                    "text": "How polished should the final product be?",
                    "type": "scale",
                    "scale_min": 1,
                    "scale_max": 5,
                    "scale_labels": ["Quick working prototype", "Fully polished & production-ready"],
                },
            ],
        }

    # Validate questions — ensure each type has required fields
    eval_obj = result.get("evaluation", {})
    if isinstance(eval_obj, dict) and "questions" in eval_obj:
        valid_questions = []
        for q in eval_obj.get("questions", []):
            if not isinstance(q, dict) or not q.get("text"):
                continue
            qtype = q.get("type", "multiple_choice")
            if qtype == "multiple_choice" and len(q.get("options", [])) < 2:
                continue
            if qtype not in ("multiple_choice", "yes_no", "text_input", "scale"):
                q["type"] = "text_input"  # safe fallback for unknown types
                q["placeholder"] = q.get("placeholder", "Type your answer...")
            valid_questions.append(q)
        eval_obj["questions"] = valid_questions

    # Ensure feedback is engaging, not generic
    feedback = result.get("feedback", "")
    if not feedback or len(feedback) < 30 or feedback.lower().strip() in ("not a good fit", "too vague", "n/a"):
        reason = result.get("reason", "")
        if len(reason) > 30:
            result["feedback"] = reason
        else:
            title = task.get("title", "your project")
            if has_answered_questions:
                result["feedback"] = (
                    f"Thanks for answering my questions about \"{title}\"! "
                    f"Your responses gave me a much clearer picture of what you're after. "
                    f"I'm confident I can deliver exactly what you need — "
                    f"I'm putting together my approach and should be ready to get started!"
                )
            else:
                result["feedback"] = (
                    f"Hey! I took a look at \"{title}\" and I'm interested. "
                    f"I've got a few quick questions below that'll help me understand exactly what you're after "
                    f"so I can hit the ground running. Should only take a minute!"
                )

    return result


def generate_claim_message(task: dict, approach: str, capabilities: list[str]) -> str:
    """Generate a fast deterministic claim message to avoid stale claims."""
    cleaned_approach = re.sub(r"\s+", " ", (approach or "").strip())
    if not cleaned_approach:
        cleaned_approach = "build the requested frontend, test the key flows, and deliver a polished result"

    focus = ", ".join(capabilities[:3]) if capabilities else "frontend implementation"
    title = (task.get("title") or "this task").strip()
    message = (
        f"I can start {title} immediately. "
        f"Plan: {cleaned_approach[:420]}. "
        f"Primary focus: {focus}."
    )
    return message[:950]


# ═══════════════════════════════════════════════════════════════════════════
# SCOUT MAIN
# ═══════════════════════════════════════════════════════════════════════════

def run_scout(
    client: OriexaClient,
    capabilities: list[str],
    attempted_tasks: dict[int, datetime] | None = None,
    claimed_task_ids: set[int] | None = None,
) -> dict:
    """
    Run one scouting cycle. Returns a result dict with:
      - action: "claimed" | "feedback" | "no_tasks" | "no_match"
      - task_id: (if claimed)
      - claim_id: (if claimed)
    """
    if attempted_tasks is None:
        attempted_tasks = {}
    if claimed_task_ids is None:
        claimed_task_ids = set()

    log_think("Browsing for open tasks...", AGENT_NAME)
    open_tasks = client.browse_tasks("open", limit=SCOUT_BROWSE_LIMIT)

    if not open_tasks:
        log_wait("No open tasks available", AGENT_NAME)
        return {"action": "no_tasks"}

    log_think(f"Found {len(open_tasks)} open task(s)", AGENT_NAME)

    best_task = None
    best_evaluation = None

    for task_summary in open_tasks:
        task_id = task_summary.get("id")
        if not task_id:
            continue

        # Don't skip claimed tasks — we still need to check for feedback responses
        # Just mark them so we don't try to claim again
        is_claimed = task_id in claimed_task_ids

        # Skip tasks we've seen recently (unless updated)
        current_updated_at = iso_to_datetime(task_summary.get("updated_at"))
        last_seen_at = attempted_tasks.get(task_id)
        if last_seen_at and current_updated_at and last_seen_at >= current_updated_at:
            continue

        # Get full task details
        try:
            detail = client.get_task(task_id)
        except Exception as e:
            log_warn(f"Failed to fetch task #{task_id}: {e}", AGENT_NAME)
            continue
        if not detail:
            continue

        # Update "last seen" mark
        task_updated = iso_to_datetime(detail.get("updated_at"))
        attempted_tasks[task_id] = task_updated or datetime.now(timezone.utc)

        # Check our remark history on this task
        remarks = detail.get("agent_remarks", [])
        my_remarks = [r for r in remarks if r.get("agent_id") == client.agent_id]

        if my_remarks:
            latest_remark = max(my_remarks, key=lambda r: r.get("timestamp", ""))
            remark_time = iso_to_datetime(latest_remark.get("timestamp"))
            if task_updated and remark_time and remark_time >= task_updated:
                if len(my_remarks) >= MAX_REMARKS_PER_TASK:
                    log_think(f"Task #{task_id}: {len(my_remarks)} remarks posted, task unchanged, skipping", AGENT_NAME)
                continue
            else:
                log_think(f"Task #{task_id} was updated since my last feedback. Re-evaluating...", AGENT_NAME)

        log_think(f"Evaluating: \"{detail.get('title', '')[:50]}\" (budget={detail.get('budget_credits')})", AGENT_NAME)

        # Fetch conversation messages so LLM sees poster replies
        try:
            conv_messages = client.get_task_messages(task_id) or []
        except Exception:
            conv_messages = []

        try:
            evaluation = evaluate_task(detail, capabilities, conv_messages)
        except Exception as e:
            log_warn(f"LLM evaluation failed: {e}", AGENT_NAME)
            continue

        # ── Two-phase feedback/claim logic ──
        # Phase 1 (initial / no answers yet): Post feedback with questions, do NOT claim.
        # Phase 2 (follow-up / poster answered): Skip remark, just claim.
        has_answered_questions = False
        for r in my_remarks:
            eval_data = r.get("evaluation")
            if eval_data:
                for q in eval_data.get("questions", []):
                    if q.get("answer"):
                        has_answered_questions = True
                        break
            if has_answered_questions:
                break
        # Only count poster conv_messages as "answers" if:
        #   a) we already posted a remark (so there were questions to answer), AND
        #   b) the poster message came AFTER our most recent remark
        # Without (a), the original task description would look like an "answer".
        if not has_answered_questions and conv_messages and my_remarks:
            latest_remark_time = max(
                (iso_to_datetime(r.get("timestamp")) for r in my_remarks),
                default=None
            )
            poster_msgs = [
                m for m in conv_messages
                if m.get("sender_type") == "poster"
                and m.get("message_type") == "text"
                and (
                    not latest_remark_time
                    or iso_to_datetime(m.get("created_at") or m.get("timestamp") or "") is not None
                    and iso_to_datetime(m.get("created_at") or m.get("timestamp") or "") > latest_remark_time
                )
            ]
            if poster_msgs:
                has_answered_questions = True

        if has_answered_questions:
            # PHASE 2: Poster answered — skip remark, proceed directly to claim
            log_think(f"  Poster answered questions for #{task_id} — proceeding to claim (no new remark)", AGENT_NAME)
            if not is_claimed and evaluation.get("should_claim") and evaluation.get("confidence") in ("high", "medium"):
                if best_task is None or evaluation.get("proposed_credits", 0) > (best_evaluation or {}).get("proposed_credits", 0):
                    best_task = detail
                    best_evaluation = evaluation
                    log_think(f"  -> Good fit! confidence={evaluation.get('confidence')}, bid={evaluation.get('proposed_credits')}", AGENT_NAME)
                    if evaluation.get("confidence") == "high":
                        log_think(f"  -> High-confidence answered task #{task_id}; claiming without scanning the rest of the batch", AGENT_NAME)
                        break
            elif is_claimed:
                log_think(f"  -> Already claimed #{task_id}", AGENT_NAME)
                attempted_tasks[task_id] = datetime.now(timezone.utc)
        else:
            # PHASE 1: Initial evaluation — post feedback with questions, do NOT claim
            # Skip feedback if we already have a pending/accepted claim on this task
            if is_claimed:
                log_think(f"  -> Already claimed #{task_id}, skipping feedback", AGENT_NAME)
                attempted_tasks[task_id] = datetime.now(timezone.utc)
                continue
            feedback = evaluation.get("feedback", "").strip().strip("\"'")
            if len(my_remarks) < MAX_REMARKS_PER_TASK and feedback:
                try:
                    remark_payload = {"remark": feedback}
                    eval_data = evaluation.get("evaluation", {})
                    if eval_data and isinstance(eval_data, dict):
                        questions = []
                        for idx, q in enumerate(eval_data.get("questions", [])[:8]):
                            if not isinstance(q, dict) or not q.get("text"):
                                continue
                            qtype = q.get("type", "multiple_choice")
                            entry = {
                                "id": q.get("id", f"q{idx}"),
                                "text": q["text"],
                                "type": qtype,
                            }
                            if qtype == "multiple_choice":
                                opts = q.get("options", [])
                                if len(opts) < 2:
                                    continue
                                entry["options"] = opts[:6]
                            elif qtype == "text_input":
                                entry["placeholder"] = q.get("placeholder", "Type your answer...")
                            elif qtype == "scale":
                                entry["scale_min"] = q.get("scale_min", 1)
                                entry["scale_max"] = q.get("scale_max", 5)
                                entry["scale_labels"] = q.get("scale_labels", ["Low", "High"])
                            questions.append(entry)

                        remark_payload["evaluation"] = {
                            "score": int(eval_data.get("score", 5)),
                            "strengths": [s for s in eval_data.get("strengths", [])[:5] if s],
                            "concerns": [c for c in eval_data.get("concerns", [])[:5] if c],
                            "questions": questions,
                        }
                    log_think(f"  Posting feedback only (score={eval_data.get('score')}, {len(remark_payload.get('evaluation', {}).get('questions', []))} Qs) — no claim yet", AGENT_NAME)
                    result = client.post_remark(task_id, remark_payload)
                    if result.get("ok"):
                        log_ok(f"Feedback posted to #{task_id} (waiting for poster to answer)", AGENT_NAME)
                    else:
                        err_info = result.get("error", {})
                        err_msg = err_info.get("message", str(err_info)) if isinstance(err_info, dict) else str(err_info)
                        log_warn(f"Failed to post feedback to #{task_id}: {err_msg}", AGENT_NAME)
                except Exception as e:
                    log_warn(f"Failed to send feedback to #{task_id}: {e}", AGENT_NAME)

            # Do NOT claim — wait for poster to answer
            log_think(f"  -> Feedback posted, waiting for poster to answer before claiming", AGENT_NAME)
            attempted_tasks[task_id] = datetime.now(timezone.utc)

    if not best_task:
        log_wait("No suitable tasks found this cycle", AGENT_NAME)
        return {"action": "no_match"}

    # Claim the best task
    task_id = best_task["id"]
    budget = best_task.get("budget_credits", 50)
    proposed = min(best_evaluation.get("proposed_credits", budget), budget)
    proposed = max(proposed, 10)

    # Re-check freshness immediately before claiming because browse/evaluation is expensive
    try:
        latest = client.get_task(task_id)
    except Exception as e:
        log_warn(f"Failed to re-check task #{task_id} before claim: {e}", AGENT_NAME)
        latest = None
    if latest and latest.get("status") != "open":
        log_warn(
            f"Task #{task_id} changed to {latest.get('status')} before claim submission; skipping stale claim",
            AGENT_NAME,
        )
        attempted_tasks[task_id] = datetime.now(timezone.utc)
        return {"action": "stale_task", "task_id": task_id, "status": latest.get("status")}

    approach = best_evaluation.get("approach", "I will deliver high-quality work.")
    try:
        message = generate_claim_message(best_task, approach, capabilities)
    except Exception:
        message = f"I can deliver this task. My approach: {approach[:200]}"

    # Ensure message fits within the 1000 character limit of CreateClaimRequest
    if len(message) > 950:
        message = message[:947] + "..."

    log_act(f"Claiming task #{task_id} for {proposed} credits...", AGENT_NAME)
    claim_resp = client.claim_task(task_id, proposed, message)

    if not claim_resp.get("ok"):
        err = (claim_resp.get("error") or {})
        log_warn(f"Claim rejected: {err.get('code', 'unknown')} — {err.get('message', '')[:100]}", AGENT_NAME)
        return {"action": "claim_rejected", "task_id": task_id, "error": err}

    claim_id = claim_resp["data"]["id"]
    log_ok(f"Claim #{claim_id} submitted for task #{task_id}! Waiting for poster to accept...", AGENT_NAME)

    return {
        "action": "claimed",
        "task_id": task_id,
        "claim_id": claim_id,
        "proposed_credits": proposed,
    }


# ═══════════════════════════════════════════════════════════════════════════
# CLI
# ═══════════════════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(description="Oriexa Scout Agent")
    parser.add_argument("--api-key", type=str, required=True, help="Agent API key")
    parser.add_argument("--base-url", type=str, default=BASE_URL, help="Oriexa API base URL")
    parser.add_argument("--capabilities", type=str, default="python,javascript,sql",
                       help="Comma-separated capabilities")
    args = parser.parse_args()

    capabilities = [c.strip() for c in args.capabilities.split(",")]
    client = OriexaClient(args.base_url, args.api_key)

    profile = client.get_profile()
    if not profile:
        log_err("Failed to authenticate with API key", AGENT_NAME)
        sys.exit(1)

    log_ok(f"Scout Agent active as: {client.agent_name} (ID: {client.agent_id})", AGENT_NAME)

    # Load existing claims to avoid double-claiming
    claimed_ids = set()
    try:
        claims = client.get_my_claims()
        for claim in claims:
            tid = claim.get("task_id") or (claim.get("task") or {}).get("id")
            if tid and claim.get("status") in ("pending", "accepted"):
                claimed_ids.add(tid)
    except Exception:
        pass

    result = run_scout(client, capabilities, claimed_task_ids=claimed_ids)
    # Output result as JSON for the orchestrator to read
    print(f"\n__RESULT__:{json.dumps(result)}", flush=True)


if __name__ == "__main__":
    main()
