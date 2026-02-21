import enum


class UserRole(str, enum.Enum):
    POSTER = "poster"
    OPERATOR = "operator"
    BOTH = "both"
    ADMIN = "admin"


class AgentStatus(str, enum.Enum):
    ACTIVE = "active"
    PAUSED = "paused"
    SUSPENDED = "suspended"


class TaskStatus(str, enum.Enum):
    OPEN = "open"
    CLAIMED = "claimed"
    IN_PROGRESS = "in_progress"
    DELIVERED = "delivered"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    DISPUTED = "disputed"


class ClaimStatus(str, enum.Enum):
    PENDING = "pending"
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    WITHDRAWN = "withdrawn"


class DeliverableStatus(str, enum.Enum):
    SUBMITTED = "submitted"
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    REVISION_REQUESTED = "revision_requested"


class TransactionType(str, enum.Enum):
    DEPOSIT = "deposit"
    BONUS = "bonus"
    PAYMENT = "payment"
    PLATFORM_FEE = "platform_fee"
    REFUND = "refund"


class WebhookEvent(str, enum.Enum):
    TASK_NEW_MATCH = "task.new_match"
    CLAIM_ACCEPTED = "claim.accepted"
    CLAIM_REJECTED = "claim.rejected"
    DELIVERABLE_ACCEPTED = "deliverable.accepted"
    DELIVERABLE_REVISION_REQUESTED = "deliverable.revision_requested"


class LlmProvider(str, enum.Enum):
    OPENROUTER = "openrouter"
    OPENAI = "openai"
    ANTHROPIC = "anthropic"


class ReviewResult(str, enum.Enum):
    PASS = "pass"
    FAIL = "fail"
    PENDING = "pending"
    SKIPPED = "skipped"


class ReviewKeySource(str, enum.Enum):
    POSTER = "poster"
    FREELANCER = "freelancer"
    NONE = "none"
