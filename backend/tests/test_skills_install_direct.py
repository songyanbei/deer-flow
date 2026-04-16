"""Tests for skill direct-install endpoints (install_from_payload, install_from_url)."""

from __future__ import annotations

import io
import json
import zipfile
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.middleware.base import BaseHTTPMiddleware


def _make_paths(base_dir: Path):
    from src.config.paths import Paths
    return Paths(base_dir=base_dir)


def _build_skill_zip(skill_name: str = "test-skill", description: str = "A test skill") -> bytes:
    """Build a minimal .skill ZIP archive in memory."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        frontmatter = f"---\nname: {skill_name}\ndescription: {description}\n---\n\nSkill body."
        zf.writestr(f"{skill_name}/SKILL.md", frontmatter)
    return buf.getvalue()


def _make_test_app():
    from src.gateway.routers.skills import router

    app = FastAPI()

    class _MockIdentityMiddleware(BaseHTTPMiddleware):
        async def dispatch(self, request, call_next):
            request.state.tenant_id = "default"
            request.state.user_id = "test-user"
            request.state.role = "admin"
            return await call_next(request)

    app.add_middleware(_MockIdentityMiddleware)
    app.include_router(router)
    return app


@pytest.fixture()
def skills_client(tmp_path: Path):
    skills_dir = tmp_path / "skills"
    (skills_dir / "public").mkdir(parents=True)
    (skills_dir / "custom").mkdir(parents=True)

    with (
        patch("src.gateway.routers.skills.get_skills_root_path", return_value=skills_dir),
        patch("src.skills.loader.get_skills_root_path", return_value=skills_dir),
    ):
        app = _make_test_app()
        with TestClient(app) as client:
            client._skills_dir = skills_dir  # type: ignore
            yield client


class TestInstallFromPayload:
    def test_basic_install(self, skills_client):
        archive = _build_skill_zip("demo-skill")
        resp = skills_client.post(
            "/api/skills/install_from_payload",
            files={"file": ("demo-skill.skill", archive, "application/octet-stream")},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        assert data["skill_name"] == "demo-skill"

        target = skills_client._skills_dir / "custom" / "demo-skill"  # type: ignore
        assert (target / "SKILL.md").exists()

    def test_install_with_source_writes_meta(self, skills_client):
        archive = _build_skill_zip("meta-skill")
        resp = skills_client.post(
            "/api/skills/install_from_payload",
            files={"file": ("meta-skill.skill", archive, "application/octet-stream")},
            data={"source": "moss-portal"},
        )
        assert resp.status_code == 200

        meta_path = skills_client._skills_dir / "custom" / "meta-skill" / ".install_meta.json"  # type: ignore
        assert meta_path.exists()
        meta = json.loads(meta_path.read_text())
        assert meta["install_source"] == "moss-portal"

    def test_duplicate_without_overwrite_409(self, skills_client):
        archive = _build_skill_zip("dup-skill")
        skills_client.post(
            "/api/skills/install_from_payload",
            files={"file": ("dup-skill.skill", archive, "application/octet-stream")},
        )
        resp = skills_client.post(
            "/api/skills/install_from_payload",
            files={"file": ("dup-skill.skill", archive, "application/octet-stream")},
        )
        assert resp.status_code == 409

    def test_overwrite_existing(self, skills_client):
        archive = _build_skill_zip("ow-skill")
        skills_client.post(
            "/api/skills/install_from_payload",
            files={"file": ("ow-skill.skill", archive, "application/octet-stream")},
        )
        resp = skills_client.post(
            "/api/skills/install_from_payload",
            files={"file": ("ow-skill.skill", archive, "application/octet-stream")},
            data={"overwrite": "true"},
        )
        assert resp.status_code == 200

    def test_invalid_zip_rejected(self, skills_client):
        resp = skills_client.post(
            "/api/skills/install_from_payload",
            files={"file": ("bad.skill", b"not-a-zip", "application/octet-stream")},
        )
        assert resp.status_code == 400

    def test_missing_frontmatter_rejected(self, skills_client):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr("bad-skill/SKILL.md", "no frontmatter here")
        resp = skills_client.post(
            "/api/skills/install_from_payload",
            files={"file": ("bad-skill.skill", buf.getvalue(), "application/octet-stream")},
        )
        assert resp.status_code == 400


class TestInstallFromUrl:
    def test_non_https_rejected(self, skills_client):
        resp = skills_client.post(
            "/api/skills/install_from_url",
            json={"url": "http://example.com/skill.skill", "checksum_sha256": "abc"},
        )
        assert resp.status_code == 400

    def test_host_not_in_allowlist(self, skills_client):
        with patch("src.gateway.routers.skills.SKILL_SOURCE_ALLOWLIST", ["trusted.example.com"]):
            resp = skills_client.post(
                "/api/skills/install_from_url",
                json={"url": "https://evil.example.com/skill.skill", "checksum_sha256": "abc"},
            )
            assert resp.status_code == 403
