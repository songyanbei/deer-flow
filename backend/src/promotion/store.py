"""JSONL-backed promotion request store.

Each tenant has its own ``promotion_requests.jsonl`` file under the
tenant directory.  The store is append-only for submissions and
rewrites on resolution (to update the matching line in-place).

Thread-safety is guaranteed by per-tenant locks.
"""

from __future__ import annotations

import json
import logging
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path

from src.config.paths import get_paths
from src.promotion.types import PromotionRequest, PromotionStatus

logger = logging.getLogger(__name__)


class PromotionStore:
    """Per-tenant promotion request store backed by a JSONL file."""

    def __init__(self) -> None:
        self._locks: dict[str, threading.Lock] = {}
        self._global_lock = threading.Lock()

    def _get_lock(self, tenant_id: str) -> threading.Lock:
        with self._global_lock:
            if tenant_id not in self._locks:
                self._locks[tenant_id] = threading.Lock()
            return self._locks[tenant_id]

    @staticmethod
    def _store_path(tenant_id: str) -> Path:
        return get_paths().tenant_dir(tenant_id) / "promotion_requests.jsonl"

    def submit(
        self,
        tenant_id: str,
        user_id: str,
        resource_type: str,
        resource_name: str,
        target_name: str | None = None,
    ) -> PromotionRequest:
        """Submit a new promotion request.  Returns the created request."""
        lock = self._get_lock(tenant_id)
        request_id = uuid.uuid4().hex[:12]
        now = datetime.now(timezone.utc).isoformat()
        req: PromotionRequest = {
            "request_id": request_id,
            "tenant_id": tenant_id,
            "user_id": user_id,
            "resource_type": resource_type,  # type: ignore[typeddict-item]
            "resource_name": resource_name,
            "target_name": target_name or resource_name,
            "status": PromotionStatus.PENDING.value,
            "created_at": now,
            "resolved_at": None,
            "resolved_by": None,
            "reason": None,
        }

        with lock:
            path = self._store_path(tenant_id)
            path.parent.mkdir(parents=True, exist_ok=True)

            # Check for duplicate pending request
            existing = self._read_all(path)
            for e in existing:
                if (
                    e.get("resource_type") == resource_type
                    and e.get("resource_name") == resource_name
                    and e.get("user_id") == user_id
                    and e.get("status") == PromotionStatus.PENDING.value
                ):
                    raise ValueError(f"A pending promotion request already exists for {resource_type} '{resource_name}'")

            with open(path, "a", encoding="utf-8") as f:
                f.write(json.dumps(req) + "\n")

        logger.info(
            "Promotion request submitted: %s %s '%s' → '%s' (tenant=%s, user=%s)",
            request_id, resource_type, resource_name, target_name or resource_name, tenant_id, user_id,
        )
        return req

    def list_pending(self, tenant_id: str) -> list[PromotionRequest]:
        """Return all pending promotion requests for a tenant."""
        lock = self._get_lock(tenant_id)
        with lock:
            all_reqs = self._read_all(self._store_path(tenant_id))
        return [r for r in all_reqs if r.get("status") == PromotionStatus.PENDING.value]

    def list_all(self, tenant_id: str) -> list[PromotionRequest]:
        """Return all promotion requests for a tenant (any status)."""
        lock = self._get_lock(tenant_id)
        with lock:
            return self._read_all(self._store_path(tenant_id))

    def list_by_user(self, tenant_id: str, user_id: str) -> list[PromotionRequest]:
        """Return all requests submitted by a specific user."""
        lock = self._get_lock(tenant_id)
        with lock:
            all_reqs = self._read_all(self._store_path(tenant_id))
        return [r for r in all_reqs if r.get("user_id") == user_id]

    def get(self, tenant_id: str, request_id: str) -> PromotionRequest | None:
        """Get a specific request by ID."""
        lock = self._get_lock(tenant_id)
        with lock:
            for r in self._read_all(self._store_path(tenant_id)):
                if r.get("request_id") == request_id:
                    return r
        return None

    def resolve(
        self,
        tenant_id: str,
        request_id: str,
        status: PromotionStatus,
        resolved_by: str,
        reason: str | None = None,
    ) -> PromotionRequest:
        """Approve or reject a pending promotion request.

        Returns the updated request.  Raises ``ValueError`` if the request
        is not found or not in pending status.
        """
        if status not in (PromotionStatus.APPROVED, PromotionStatus.REJECTED):
            raise ValueError(f"Invalid resolution status: {status}")

        lock = self._get_lock(tenant_id)
        now = datetime.now(timezone.utc).isoformat()

        with lock:
            path = self._store_path(tenant_id)
            all_reqs = self._read_all(path)

            target: PromotionRequest | None = None
            for r in all_reqs:
                if r.get("request_id") == request_id:
                    target = r
                    break

            if target is None:
                raise ValueError(f"Promotion request '{request_id}' not found")
            if target.get("status") != PromotionStatus.PENDING.value:
                raise ValueError(f"Request '{request_id}' is already resolved ({target.get('status')})")

            target["status"] = status.value
            target["resolved_at"] = now
            target["resolved_by"] = resolved_by
            target["reason"] = reason

            # Rewrite the entire file
            self._write_all(path, all_reqs)

        logger.info(
            "Promotion request resolved: %s → %s by %s (reason=%s)",
            request_id, status.value, resolved_by, reason,
        )
        return target

    @staticmethod
    def _read_all(path: Path) -> list[PromotionRequest]:
        if not path.exists():
            return []
        results: list[PromotionRequest] = []
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line:
                try:
                    results.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
        return results

    @staticmethod
    def _write_all(path: Path, requests: list[PromotionRequest]) -> None:
        with open(path, "w", encoding="utf-8") as f:
            for r in requests:
                f.write(json.dumps(r) + "\n")


# ── Module-level singleton ──────────────────────────────────────────────

_store: PromotionStore | None = None


def get_promotion_store() -> PromotionStore:
    global _store
    if _store is None:
        _store = PromotionStore()
    return _store
