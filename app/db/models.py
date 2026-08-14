from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, ENUM as PgEnum
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship
from sqlalchemy.sql import func

from app.db.enums import (
    AgentRole,
    AgentStatus,
    ClaimStatus,
    DeliverableStatus,
    LlmProvider,
    MessageDirection,
    OrchTaskStatus,
    ReviewKeySource,
    ReviewResult,
    SubtaskStatus,
    TaskMessageSenderType,
    TaskMessageType,
    TaskStatus,
    TransactionType,
    UserRole,
    WebhookEvent,
)

# Create PostgreSQL ENUM types
user_role_enum = PgEnum(
    "poster", "operator", "both", "admin",
    name="user_role", create_type=False,
)
agent_status_enum = PgEnum(
    "active", "paused", "suspended",
    name="agent_status", create_type=False,
)
task_status_enum = PgEnum(
    "open", "claimed", "in_progress", "delivered", "completed", "cancelled", "disputed",
    name="task_status", create_type=False,
)
claim_status_enum = PgEnum(
    "pending", "accepted", "rejected", "withdrawn",
    name="claim_status", create_type=False,
)
deliverable_status_enum = PgEnum(
    "submitted", "accepted", "rejected", "revision_requested",
    name="deliverable_status", create_type=False,
)
transaction_type_enum = PgEnum(
    "deposit", "bonus", "payment", "platform_fee", "refund",
    name="transaction_type", create_type=False,
)
webhook_event_enum = PgEnum(
    "task.new_match", "task.updated", "claim.created", "claim.accepted", "claim.rejected",
    "deliverable.submitted", "deliverable.accepted", "deliverable.revision_requested",
    "message.created",
    name="webhook_event", create_type=False,
)
llm_provider_enum = PgEnum(
    "opencode-go", "openrouter", "openai", "anthropic",
    name="llm_provider", create_type=False,
)
review_result_enum = PgEnum(
    "pass", "fail", "pending", "skipped",
    name="review_result", create_type=False,
)
review_key_source_enum = PgEnum(
    "poster", "freelancer", "none",
    name="review_key_source", create_type=False,
)

# Orchestrator ENUM types
orch_task_status_enum = PgEnum(
    "pending", "claiming", "clarifying", "planning", "executing",
    "reviewing", "delivering", "completed", "failed",
    name="orch_task_status", create_type=False,
)
agent_role_enum = PgEnum(
    "triage", "clarification", "planning", "execution", "complex_task", "review",
    name="agent_role", create_type=False,
)
subtask_status_enum = PgEnum(
    "pending", "in_progress", "completed", "failed", "skipped",
    name="subtask_status", create_type=False,
)
message_direction_enum = PgEnum(
    "agent_to_poster", "poster_to_agent",
    name="message_direction", create_type=False,
)
task_msg_sender_type_enum = PgEnum(
    "poster", "agent", "system",
    name="task_msg_sender_type", create_type=False,
)
task_msg_type_enum = PgEnum(
    "text", "question", "evaluation", "attachment", "claim_proposal",
    "status_change", "revision_request", "remark",
    name="task_msg_type", create_type=False,
)


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    password_hash: Mapped[str | None] = mapped_column(String(255), nullable=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[str] = mapped_column(user_role_enum, nullable=False, server_default="both")
    avatar_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    bio: Mapped[str | None] = mapped_column(Text, nullable=True)
    credit_balance: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    agents: Mapped[list["Agent"]] = relationship(back_populates="operator")
    tasks: Mapped[list["Task"]] = relationship(back_populates="poster")
    credit_transactions: Mapped[list["CreditTransaction"]] = relationship(back_populates="user", foreign_keys="[CreditTransaction.user_id]")


class Agent(Base):
    __tablename__ = "agents"
    __table_args__ = (
        Index("agents_operator_id_idx", "operator_id"),
        Index("agents_status_idx", "status"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    operator_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    capabilities: Mapped[list[str]] = mapped_column(
        ARRAY(Text), nullable=False, server_default="{}"
    )
    category_ids: Mapped[list[int]] = mapped_column(
        ARRAY(Integer), nullable=False, server_default="{}"
    )
    hourly_rate_credits: Mapped[int | None] = mapped_column(Integer, nullable=True)
    api_key_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    api_key_prefix: Mapped[str | None] = mapped_column(String(14), nullable=True)
    webhook_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    status: Mapped[str] = mapped_column(
        agent_status_enum, nullable=False, server_default="active"
    )
    reputation_score: Mapped[float] = mapped_column(
        Float, nullable=False, server_default="50.0"
    )
    tasks_completed: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default="0"
    )
    avg_rating: Mapped[float | None] = mapped_column(Float, nullable=True)
    freelancer_llm_key_encrypted: Mapped[str | None] = mapped_column(Text, nullable=True)
    freelancer_llm_provider: Mapped[str | None] = mapped_column(
        llm_provider_enum, nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    operator: Mapped["User"] = relationship(back_populates="agents")
    claims: Mapped[list["TaskClaim"]] = relationship(back_populates="agent")
    deliverables: Mapped[list["Deliverable"]] = relationship(back_populates="agent")
    webhooks: Mapped[list["Webhook"]] = relationship(back_populates="agent")


class Category(Base):
    __tablename__ = "categories"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    slug: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    icon: Mapped[str | None] = mapped_column(String(50), nullable=True)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")


class Task(Base):
    __tablename__ = "tasks"
    __table_args__ = (
        Index("tasks_status_idx", "status"),
        Index("tasks_poster_id_idx", "poster_id"),
        Index("tasks_category_id_idx", "category_id"),
        Index("tasks_created_at_idx", "created_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    poster_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id"), nullable=False
    )
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    requirements: Mapped[str | None] = mapped_column(Text, nullable=True)
    budget_credits: Mapped[int] = mapped_column(Integer, nullable=False)
    category_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("categories.id"), nullable=True
    )
    status: Mapped[str] = mapped_column(
        task_status_enum, nullable=False, server_default="open"
    )
    claimed_by_agent_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("agents.id"), nullable=True
    )
    deadline: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    max_revisions: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default="2"
    )
    auto_review_enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="false"
    )
    poster_llm_key_encrypted: Mapped[str | None] = mapped_column(Text, nullable=True)
    poster_llm_provider: Mapped[str | None] = mapped_column(
        llm_provider_enum, nullable=True
    )
    poster_max_reviews: Mapped[int | None] = mapped_column(Integer, nullable=True)
    poster_reviews_used: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default="0"
    )
    agent_remarks: Mapped[list[dict]] = mapped_column(
        JSONB, nullable=False, server_default="[]"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    poster: Mapped["User"] = relationship(back_populates="tasks")
    category: Mapped["Category | None"] = relationship()
    claims: Mapped[list["TaskClaim"]] = relationship(back_populates="task")
    deliverables: Mapped[list["Deliverable"]] = relationship(back_populates="task")


class TaskClaim(Base):
    __tablename__ = "task_claims"
    __table_args__ = (
        Index("task_claims_task_id_idx", "task_id"),
        Index("task_claims_agent_id_idx", "agent_id"),
        Index("task_claims_task_agent_status_idx", "task_id", "agent_id", "status"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    task_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("tasks.id"), nullable=False
    )
    agent_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("agents.id"), nullable=False
    )
    proposed_credits: Mapped[int] = mapped_column(Integer, nullable=False)
    message: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(
        claim_status_enum, nullable=False, server_default="pending"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    task: Mapped["Task"] = relationship(back_populates="claims")
    agent: Mapped["Agent"] = relationship(back_populates="claims")


class Deliverable(Base):
    __tablename__ = "deliverables"
    __table_args__ = (
        Index("deliverables_task_id_idx", "task_id"),
        Index("deliverables_task_agent_idx", "task_id", "agent_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    task_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("tasks.id"), nullable=False
    )
    agent_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("agents.id"), nullable=False
    )
    content: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(
        deliverable_status_enum, nullable=False, server_default="submitted"
    )
    revision_notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    revision_number: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default="1"
    )
    submitted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    task: Mapped["Task"] = relationship(back_populates="deliverables")
    agent: Mapped["Agent"] = relationship(back_populates="deliverables")


class Review(Base):
    __tablename__ = "reviews"
    __table_args__ = (
        UniqueConstraint("task_id", name="reviews_task_id_unique"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    task_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("tasks.id"), nullable=False
    )
    reviewer_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id"), nullable=False
    )
    agent_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("agents.id"), nullable=False
    )
    rating: Mapped[int] = mapped_column(Integer, nullable=False)
    quality_score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    speed_score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    comment: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class CreditTransaction(Base):
    __tablename__ = "credit_transactions"
    __table_args__ = (
        Index("credit_transactions_user_id_idx", "user_id"),
        Index("credit_transactions_created_at_idx", "created_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id"), nullable=False
    )
    amount: Mapped[int] = mapped_column(Integer, nullable=False)
    type: Mapped[str] = mapped_column(transaction_type_enum, nullable=False)
    task_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("tasks.id"), nullable=True
    )
    counterparty_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("users.id"), nullable=True
    )
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    balance_after: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    user: Mapped["User"] = relationship(back_populates="credit_transactions", foreign_keys=[user_id])


class Webhook(Base):
    __tablename__ = "webhooks"
    __table_args__ = (
        Index("webhooks_agent_id_idx", "agent_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    agent_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("agents.id"), nullable=False
    )
    url: Mapped[str] = mapped_column(String(500), nullable=False)
    secret: Mapped[str] = mapped_column(String(64), nullable=False)
    events: Mapped[list[str]] = mapped_column(
        ARRAY(webhook_event_enum), nullable=False
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="true"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    agent: Mapped["Agent"] = relationship(back_populates="webhooks")
    deliveries: Mapped[list["WebhookDelivery"]] = relationship(back_populates="webhook")


class WebhookDelivery(Base):
    __tablename__ = "webhook_deliveries"
    __table_args__ = (
        Index("webhook_deliveries_webhook_id_idx", "webhook_id"),
        Index("webhook_deliveries_attempted_at_idx", "attempted_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    webhook_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("webhooks.id"), nullable=False
    )
    event: Mapped[str] = mapped_column(webhook_event_enum, nullable=False)
    payload: Mapped[str] = mapped_column(Text, nullable=False)
    response_status: Mapped[int | None] = mapped_column(Integer, nullable=True)
    response_body: Mapped[str | None] = mapped_column(Text, nullable=True)
    success: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="false"
    )
    attempted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)

    webhook: Mapped["Webhook"] = relationship(back_populates="deliveries")


class IdempotencyKey(Base):
    __tablename__ = "idempotency_keys"
    __table_args__ = (
        UniqueConstraint("agent_id", "idempotency_key", name="idempotency_keys_agent_key_idx"),
        Index("idempotency_keys_expires_at_idx", "expires_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    agent_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("agents.id"), nullable=False
    )
    idempotency_key: Mapped[str] = mapped_column(String(255), nullable=False)
    request_path: Mapped[str] = mapped_column(String(500), nullable=False)
    request_body_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    response_status: Mapped[int | None] = mapped_column(Integer, nullable=True)
    response_body: Mapped[str | None] = mapped_column(Text, nullable=True)
    locked_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class SubmissionAttempt(Base):
    __tablename__ = "submission_attempts"
    __table_args__ = (
        Index("submission_attempts_task_id_idx", "task_id"),
        Index("submission_attempts_agent_id_idx", "agent_id"),
        Index("submission_attempts_task_agent_idx", "task_id", "agent_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    task_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("tasks.id"), nullable=False
    )
    agent_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("agents.id"), nullable=False
    )
    deliverable_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("deliverables.id"), nullable=True
    )
    attempt_number: Mapped[int] = mapped_column(Integer, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    submitted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    review_result: Mapped[str] = mapped_column(
        review_result_enum, nullable=False, server_default="pending"
    )
    review_feedback: Mapped[str | None] = mapped_column(Text, nullable=True)
    review_scores: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    reviewed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    review_key_source: Mapped[str] = mapped_column(
        review_key_source_enum, nullable=False, server_default="none"
    )
    llm_model_used: Mapped[str | None] = mapped_column(String(200), nullable=True)


# ---------------------------------------------------------------------------
# Task Messages (conversation timeline)
# ---------------------------------------------------------------------------

class TaskMessage(Base):
    """Unified conversation timeline for a task."""
    __tablename__ = "task_messages"
    __table_args__ = (
        Index("task_messages_task_id_idx", "task_id"),
        Index("task_messages_task_created_idx", "task_id", "created_at"),
        Index("task_messages_parent_id_idx", "parent_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    task_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("tasks.id"), nullable=False
    )
    sender_type: Mapped[str] = mapped_column(task_msg_sender_type_enum, nullable=False)
    sender_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    sender_name: Mapped[str] = mapped_column(String(255), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    message_type: Mapped[str] = mapped_column(
        task_msg_type_enum, nullable=False, server_default="text"
    )
    structured_data: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    parent_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("task_messages.id"), nullable=True
    )
    claim_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("task_claims.id"), nullable=True
    )
    is_read: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="false"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


# ---------------------------------------------------------------------------
# Orchestrator Models (orch_ prefix)
# ---------------------------------------------------------------------------

class OrchTaskExecution(Base):
    """Tracks each task through the orchestrator pipeline."""
    __tablename__ = "orch_task_executions"
    __table_args__ = (
        Index("orch_task_exec_status_idx", "status"),
        Index("orch_task_exec_task_id_idx", "oriexa_task_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    oriexa_task_id: Mapped[int] = mapped_column(Integer, nullable=False, unique=True)
    status: Mapped[str] = mapped_column(
        orch_task_status_enum, nullable=False, server_default="pending"
    )
    task_snapshot: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default="{}")
    graph_thread_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    workspace_path: Mapped[str | None] = mapped_column(String(500), nullable=True)
    total_tokens_used: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    total_cost_usd: Mapped[float] = mapped_column(Float, nullable=False, server_default="0.0")
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    claimed_credits: Mapped[int | None] = mapped_column(Integer, nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    subtasks: Mapped[list["OrchSubtask"]] = relationship(back_populates="execution")
    messages: Mapped[list["OrchMessage"]] = relationship(back_populates="execution")
    agent_runs: Mapped[list["OrchAgentRun"]] = relationship(back_populates="execution")


class OrchSubtask(Base):
    """Subtasks created by PlanningAgent for task decomposition."""
    __tablename__ = "orch_subtasks"
    __table_args__ = (
        Index("orch_subtasks_execution_id_idx", "execution_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    execution_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("orch_task_executions.id"), nullable=False
    )
    order_index: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(
        subtask_status_enum, nullable=False, server_default="pending"
    )
    result: Mapped[str | None] = mapped_column(Text, nullable=True)
    files_changed: Mapped[list] = mapped_column(JSONB, nullable=False, server_default="[]")
    depends_on: Mapped[list] = mapped_column(JSONB, nullable=False, server_default="[]")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    execution: Mapped["OrchTaskExecution"] = relationship(back_populates="subtasks")


class OrchMessage(Base):
    """Messages exchanged between agents and task posters."""
    __tablename__ = "orch_messages"
    __table_args__ = (
        Index("orch_messages_execution_id_idx", "execution_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    execution_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("orch_task_executions.id"), nullable=False
    )
    direction: Mapped[str] = mapped_column(message_direction_enum, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    deliverable_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    thread_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    execution: Mapped["OrchTaskExecution"] = relationship(back_populates="messages")


class OrchAgentRun(Base):
    """Audit log of every agent invocation in the orchestrator."""
    __tablename__ = "orch_agent_runs"
    __table_args__ = (
        Index("orch_agent_runs_execution_id_idx", "execution_id"),
        Index("orch_agent_runs_role_idx", "role"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    execution_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("orch_task_executions.id"), nullable=False
    )
    role: Mapped[str] = mapped_column(agent_role_enum, nullable=False)
    model_used: Mapped[str] = mapped_column(String(200), nullable=False)
    prompt_tokens: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    completion_tokens: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    duration_ms: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    success: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    input_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    output_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    execution: Mapped["OrchTaskExecution"] = relationship(back_populates="agent_runs")
