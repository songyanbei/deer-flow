"""Promotion request data types."""

from __future__ import annotations

from enum import Enum
from typing import Literal, TypedDict


class PromotionStatus(str, Enum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"


class PromotionRequest(TypedDict, total=False):
    """A request to promote a personal resource to the tenant layer."""

    request_id: str
    tenant_id: str
    user_id: str
    resource_type: Literal["agent", "skill"]
    resource_name: str
    target_name: str  # name at tenant layer (usually same as resource_name)
    status: str  # PromotionStatus value
    created_at: str  # ISO 8601
    resolved_at: str | None  # ISO 8601 or None
    resolved_by: str | None  # user_id of admin who resolved
    reason: str | None  # admin comment on approve/reject
