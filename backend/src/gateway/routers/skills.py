import hashlib
import json
import logging
import os
import re
import shutil
import tempfile
import zipfile
from pathlib import Path
from urllib.parse import urlparse

import yaml
from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from pydantic import BaseModel, Field

from src.config._config_lock import atomic_write_json, tenant_config_lock
from src.config.extensions_config import ExtensionsConfig, SkillStateConfig, get_extensions_config, reload_extensions_config
from src.gateway.dependencies import get_tenant_id, get_user_id, require_role
from src.gateway.path_utils import resolve_thread_virtual_path_ctx
from src.gateway.thread_context import resolve_thread_context
from src.skills import Skill, load_skills
from src.skills.loader import get_skills_root_path

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["skills"])

SKILL_PAYLOAD_MAX_BYTES = int(os.getenv("SKILL_PAYLOAD_MAX_BYTES", str(50 * 1024 * 1024)))
SKILL_SOURCE_ALLOWLIST = [h.strip() for h in os.getenv("SKILL_SOURCE_ALLOWLIST", "").split(",") if h.strip()]
SKILL_DOWNLOAD_TIMEOUT = int(os.getenv("SKILL_DOWNLOAD_TIMEOUT", "30"))

# ── Response / request models ─────────────────────────────────────────


class SkillResponse(BaseModel):
    """Response model for skill information."""

    name: str = Field(..., description="Name of the skill")
    description: str = Field(..., description="Description of what the skill does")
    license: str | None = Field(None, description="License information")
    category: str = Field(..., description="Category of the skill (public or custom)")
    enabled: bool = Field(default=True, description="Whether this skill is enabled")
    source: str | None = Field(default=None, description="Resource source layer (platform, tenant, personal) — only in merged view")
    install_source: str | None = Field(default=None, description="Origin that installed this skill (e.g. 'moss-portal')")


class SkillsListResponse(BaseModel):
    """Response model for listing all skills."""

    skills: list[SkillResponse]


class SkillUpdateRequest(BaseModel):
    """Request model for updating a skill."""

    enabled: bool = Field(..., description="Whether to enable or disable the skill")


class SkillInstallRequest(BaseModel):
    """Request model for installing a skill from a .skill file."""

    thread_id: str = Field(..., description="The thread ID where the .skill file is located")
    path: str = Field(..., description="Virtual path to the .skill file (e.g., mnt/user-data/outputs/my-skill.skill)")


class SkillInstallResponse(BaseModel):
    """Response model for skill installation."""

    success: bool = Field(..., description="Whether the installation was successful")
    skill_name: str = Field(..., description="Name of the installed skill")
    message: str = Field(..., description="Installation result message")
    source: str | None = Field(default=None, description="Install source origin")


class SkillInstallFromUrlRequest(BaseModel):
    """Request model for installing a skill from a URL."""

    url: str = Field(..., description="HTTPS URL to download the .skill archive")
    source: str | None = Field(default=None, description="Install source origin (e.g. 'moss-portal')")
    checksum_sha256: str = Field(..., description="Expected SHA-256 hex digest of the downloaded file")
    overwrite: bool = Field(default=False, description="Overwrite existing skill if present")


# ── Frontmatter validation ────────────────────────────────────────────

ALLOWED_FRONTMATTER_PROPERTIES = {"name", "description", "license", "allowed-tools", "metadata"}


def _validate_skill_frontmatter(skill_dir: Path) -> tuple[bool, str, str | None]:
    """Validate a skill directory's SKILL.md frontmatter.

    Returns:
        Tuple of (is_valid, message, skill_name).
    """
    skill_md = skill_dir / "SKILL.md"
    if not skill_md.exists():
        return False, "SKILL.md not found", None

    content = skill_md.read_text()
    if not content.startswith("---"):
        return False, "No YAML frontmatter found", None

    match = re.match(r"^---\n(.*?)\n---", content, re.DOTALL)
    if not match:
        return False, "Invalid frontmatter format", None

    frontmatter_text = match.group(1)

    try:
        frontmatter = yaml.safe_load(frontmatter_text)
        if not isinstance(frontmatter, dict):
            return False, "Frontmatter must be a YAML dictionary", None
    except yaml.YAMLError as e:
        return False, f"Invalid YAML in frontmatter: {e}", None

    unexpected_keys = set(frontmatter.keys()) - ALLOWED_FRONTMATTER_PROPERTIES
    if unexpected_keys:
        return False, f"Unexpected key(s) in SKILL.md frontmatter: {', '.join(sorted(unexpected_keys))}", None

    if "name" not in frontmatter:
        return False, "Missing 'name' in frontmatter", None
    if "description" not in frontmatter:
        return False, "Missing 'description' in frontmatter", None

    name = frontmatter.get("name", "")
    if not isinstance(name, str):
        return False, f"Name must be a string, got {type(name).__name__}", None
    name = name.strip()
    if not name:
        return False, "Name cannot be empty", None

    if not re.match(r"^[a-z0-9-]+$", name):
        return False, f"Name '{name}' should be hyphen-case (lowercase letters, digits, and hyphens only)", None
    if name.startswith("-") or name.endswith("-") or "--" in name:
        return False, f"Name '{name}' cannot start/end with hyphen or contain consecutive hyphens", None
    if len(name) > 64:
        return False, f"Name is too long ({len(name)} characters). Maximum is 64 characters.", None

    description = frontmatter.get("description", "")
    if not isinstance(description, str):
        return False, f"Description must be a string, got {type(description).__name__}", None
    description = description.strip()
    if description:
        if "<" in description or ">" in description:
            return False, "Description cannot contain angle brackets (< or >)", None
        if len(description) > 1024:
            return False, f"Description is too long ({len(description)} characters). Maximum is 1024 characters.", None

    return True, "Skill is valid!", name


# ── Internal install logic ────────────────────────────────────────────


def _resolve_skill_install_dir(tenant_id: str) -> Path:
    """Return the target skills directory for installs."""
    if tenant_id and tenant_id != "default":
        from src.config.paths import get_paths
        return get_paths().tenant_dir(tenant_id) / "skills"
    return get_skills_root_path() / "custom"


def _install_skill_from_archive(
    archive_path: Path,
    tenant_id: str,
    install_source: str | None = None,
    overwrite: bool = False,
) -> tuple[str, Path]:
    """Extract, validate, and install a .skill archive.

    Returns (skill_name, target_dir).
    """
    if not archive_path.suffix == ".skill":
        raise HTTPException(status_code=400, detail="File must have .skill extension")

    if not zipfile.is_zipfile(archive_path):
        raise HTTPException(status_code=400, detail="File is not a valid ZIP archive")

    custom_skills_dir = _resolve_skill_install_dir(tenant_id)
    custom_skills_dir.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory() as temp_dir:
        temp_path = Path(temp_dir)

        with zipfile.ZipFile(archive_path, "r") as zip_ref:
            zip_ref.extractall(temp_path)

        extracted_items = list(temp_path.iterdir())
        if len(extracted_items) == 0:
            raise HTTPException(status_code=400, detail="Skill archive is empty")

        if len(extracted_items) == 1 and extracted_items[0].is_dir():
            skill_dir = extracted_items[0]
        else:
            skill_dir = temp_path

        is_valid, message, skill_name = _validate_skill_frontmatter(skill_dir)
        if not is_valid:
            raise HTTPException(status_code=400, detail=f"Invalid skill: {message}")

        if not skill_name:
            raise HTTPException(status_code=400, detail="Could not determine skill name")

        target_dir = custom_skills_dir / skill_name
        if target_dir.exists():
            if not overwrite:
                raise HTTPException(status_code=409, detail=f"Skill '{skill_name}' already exists. Use overwrite=true or remove it first.")
            shutil.rmtree(target_dir)

        shutil.copytree(skill_dir, target_dir)

        if install_source:
            meta = {"install_source": install_source}
            meta_path = target_dir / ".install_meta.json"
            with open(meta_path, "w", encoding="utf-8") as f:
                json.dump(meta, f, indent=2)

    return skill_name, target_dir


def _skill_to_response(skill: Skill) -> SkillResponse:
    """Convert a Skill object to a SkillResponse."""
    install_source = None
    meta_path = skill.skill_dir / ".install_meta.json"
    if meta_path.exists():
        try:
            with open(meta_path, encoding="utf-8") as f:
                meta = json.load(f)
            install_source = meta.get("install_source")
        except Exception:
            pass

    return SkillResponse(
        name=skill.name,
        description=skill.description,
        license=skill.license,
        category=skill.category,
        enabled=skill.enabled,
        source=getattr(skill, "source", None),
        install_source=install_source,
    )


# ── Endpoints ─────────────────────────────────────────────────────────


@router.get(
    "/skills",
    response_model=SkillsListResponse,
    summary="List All Skills",
    description="Retrieve a list of all available skills from both public and custom directories.",
)
async def list_skills(
    tenant_id: str = Depends(get_tenant_id),
    user_id: str = Depends(get_user_id),
    view: str | None = None,
) -> SkillsListResponse:
    """List all available skills (platform + tenant-scoped, optionally merged with personal)."""
    try:
        uid = user_id if view == "merged" else None
        skills = load_skills(enabled_only=False, tenant_id=tenant_id, user_id=uid)
        return SkillsListResponse(skills=[_skill_to_response(skill) for skill in skills])
    except Exception as e:
        logger.error("Failed to load skills: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to load skills: {str(e)}")


@router.get(
    "/skills/{skill_name}",
    response_model=SkillResponse,
    summary="Get Skill Details",
    description="Retrieve detailed information about a specific skill by its name.",
)
async def get_skill(skill_name: str, tenant_id: str = Depends(get_tenant_id)) -> SkillResponse:
    """Get a specific skill by name."""
    try:
        skills = load_skills(enabled_only=False, tenant_id=tenant_id)
        skill = next((s for s in skills if s.name == skill_name), None)

        if skill is None:
            raise HTTPException(status_code=404, detail=f"Skill '{skill_name}' not found")

        return _skill_to_response(skill)
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Failed to get skill %s: %s", skill_name, e, exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to get skill: {str(e)}")


@router.put(
    "/skills/{skill_name}",
    response_model=SkillResponse,
    summary="Update Skill",
    description="Update a skill's enabled status by modifying the skills_state_config.json file.",
    dependencies=[require_role("admin", "owner")],
)
async def update_skill(skill_name: str, request: SkillUpdateRequest, tenant_id: str = Depends(get_tenant_id)) -> SkillResponse:
    """Update a skill's enabled status."""
    try:
        skills = load_skills(enabled_only=False, tenant_id=tenant_id)
        skill = next((s for s in skills if s.name == skill_name), None)

        if skill is None:
            raise HTTPException(status_code=404, detail=f"Skill '{skill_name}' not found")

        config_path = _resolve_extensions_config_path(tenant_id)

        async with tenant_config_lock(tenant_id, "skill", lockfile=config_path.parent / ".skill.lock"):
            if tenant_id and tenant_id != "default":
                raw = _load_raw(config_path)
                skills_section = raw.get("skills", {})
                skills_section[skill_name] = {"enabled": request.enabled}
                raw["skills"] = skills_section
                atomic_write_json(config_path, raw)
            else:
                extensions_config = get_extensions_config()
                extensions_config.skills[skill_name] = SkillStateConfig(enabled=request.enabled)

                config_data = {
                    "mcpServers": {name: server.model_dump() for name, server in extensions_config.mcp_servers.items()},
                    "skills": {name: {"enabled": skill_config.enabled} for name, skill_config in extensions_config.skills.items()},
                }
                atomic_write_json(config_path, config_data)
                reload_extensions_config()

        logger.info("Skills configuration updated and saved to: %s", config_path)

        skills = load_skills(enabled_only=False, tenant_id=tenant_id)
        updated_skill = next((s for s in skills if s.name == skill_name), None)

        if updated_skill is None:
            raise HTTPException(status_code=500, detail=f"Failed to reload skill '{skill_name}' after update")

        logger.info("Skill '%s' enabled status updated to %s", skill_name, request.enabled)
        return _skill_to_response(updated_skill)

    except HTTPException:
        raise
    except Exception as e:
        logger.error("Failed to update skill %s: %s", skill_name, e, exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to update skill: {str(e)}")


@router.post(
    "/skills/install",
    response_model=SkillInstallResponse,
    summary="Install Skill (thread upload)",
    description="Install a skill from a .skill file (ZIP archive) located in the thread's user-data directory.",
    dependencies=[require_role("admin", "owner")],
)
async def install_skill(request: SkillInstallRequest, tenant_id: str = Depends(get_tenant_id), user_id: str = Depends(get_user_id)) -> SkillInstallResponse:
    """Install a skill from a .skill file previously uploaded to a thread."""
    try:
        ctx = resolve_thread_context(request.thread_id, tenant_id, user_id)
        skill_file_path = resolve_thread_virtual_path_ctx(ctx, request.path)

        if not skill_file_path.exists():
            raise HTTPException(status_code=404, detail=f"Skill file not found: {request.path}")
        if not skill_file_path.is_file():
            raise HTTPException(status_code=400, detail=f"Path is not a file: {request.path}")

        skill_name, _target_dir = _install_skill_from_archive(skill_file_path, tenant_id)
        logger.info("Skill '%s' installed successfully to %s", skill_name, _target_dir)
        return SkillInstallResponse(success=True, skill_name=skill_name, message=f"Skill '{skill_name}' installed successfully")

    except HTTPException:
        raise
    except Exception as e:
        logger.error("Failed to install skill: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to install skill: {str(e)}")


@router.post(
    "/skills/install_from_payload",
    response_model=SkillInstallResponse,
    summary="Install Skill (direct upload)",
    description="Install a skill from a directly uploaded .skill file (multipart).",
    dependencies=[require_role("admin", "owner")],
)
async def install_skill_from_payload(
    file: UploadFile = File(..., description="The .skill ZIP archive"),
    source: str | None = Form(default=None, description="Install source origin (e.g. 'moss-portal')"),
    overwrite: bool = Form(default=False, description="Overwrite existing skill if present"),
    tenant_id: str = Depends(get_tenant_id),
) -> SkillInstallResponse:
    """Install a skill from a directly uploaded archive (no thread context required)."""
    try:
        content = await file.read()
        if len(content) > SKILL_PAYLOAD_MAX_BYTES:
            raise HTTPException(status_code=413, detail=f"File too large. Maximum size is {SKILL_PAYLOAD_MAX_BYTES} bytes.")

        if len(content) < 4 or content[:4] != b"PK\x03\x04":
            raise HTTPException(status_code=400, detail="Uploaded file is not a valid ZIP archive")

        with tempfile.NamedTemporaryFile(suffix=".skill", delete=False) as tmp:
            tmp.write(content)
            tmp_path = Path(tmp.name)

        try:
            skill_name, target_dir = _install_skill_from_archive(tmp_path, tenant_id, install_source=source, overwrite=overwrite)
        finally:
            tmp_path.unlink(missing_ok=True)

        logger.info("Skill '%s' installed from payload (source=%s) to %s", skill_name, source, target_dir)
        return SkillInstallResponse(success=True, skill_name=skill_name, message=f"Skill '{skill_name}' installed successfully", source=source)

    except HTTPException:
        raise
    except Exception as e:
        logger.error("Failed to install skill from payload: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to install skill: {str(e)}")


@router.post(
    "/skills/install_from_url",
    response_model=SkillInstallResponse,
    summary="Install Skill (from URL)",
    description="Download and install a skill from a whitelisted HTTPS URL.",
    dependencies=[require_role("admin", "owner")],
)
async def install_skill_from_url(
    request: SkillInstallFromUrlRequest,
    tenant_id: str = Depends(get_tenant_id),
) -> SkillInstallResponse:
    """Download a .skill archive from a trusted URL and install it."""
    import httpx

    parsed = urlparse(request.url)
    if parsed.scheme != "https":
        raise HTTPException(status_code=400, detail="Only https:// URLs are allowed")

    if SKILL_SOURCE_ALLOWLIST and parsed.hostname not in SKILL_SOURCE_ALLOWLIST:
        raise HTTPException(status_code=403, detail=f"Host '{parsed.hostname}' is not in the allowed source list")

    try:
        async with httpx.AsyncClient(timeout=SKILL_DOWNLOAD_TIMEOUT, follow_redirects=False) as client:
            resp = await client.get(request.url)

            if resp.is_redirect:
                redirect_url = resp.headers.get("location", "")
                redirect_parsed = urlparse(redirect_url)
                if redirect_parsed.hostname != parsed.hostname:
                    raise HTTPException(status_code=403, detail=f"Redirect to different host '{redirect_parsed.hostname}' is not allowed")
                resp = await client.get(redirect_url)

            resp.raise_for_status()
            content = resp.content

        if len(content) > SKILL_PAYLOAD_MAX_BYTES:
            raise HTTPException(status_code=413, detail=f"Downloaded file too large. Maximum size is {SKILL_PAYLOAD_MAX_BYTES} bytes.")

        actual_sha256 = hashlib.sha256(content).hexdigest()
        if actual_sha256 != request.checksum_sha256:
            raise HTTPException(status_code=422, detail=f"Checksum mismatch: expected {request.checksum_sha256}, got {actual_sha256}")

        with tempfile.NamedTemporaryFile(suffix=".skill", delete=False) as tmp:
            tmp.write(content)
            tmp_path = Path(tmp.name)

        try:
            skill_name, target_dir = _install_skill_from_archive(tmp_path, tenant_id, install_source=request.source, overwrite=request.overwrite)
        finally:
            tmp_path.unlink(missing_ok=True)

        logger.info("Skill '%s' installed from URL (source=%s) to %s", skill_name, request.source, target_dir)
        return SkillInstallResponse(success=True, skill_name=skill_name, message=f"Skill '{skill_name}' installed successfully", source=request.source)

    except HTTPException:
        raise
    except httpx.HTTPStatusError as e:
        raise HTTPException(status_code=502, detail=f"Failed to download skill: HTTP {e.response.status_code}")
    except httpx.TimeoutException:
        raise HTTPException(status_code=504, detail="Skill download timed out")
    except Exception as e:
        logger.error("Failed to install skill from URL: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to install skill: {str(e)}")


# ── Utilities ─────────────────────────────────────────────────────────


def _resolve_extensions_config_path(tenant_id: str) -> Path:
    if tenant_id and tenant_id != "default":
        from src.config.paths import get_paths
        path = get_paths().tenant_dir(tenant_id) / "extensions_config.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        return path
    config_path = ExtensionsConfig.resolve_config_path()
    if config_path is None:
        config_path = Path.cwd().parent / "extensions_config.json"
    return config_path


def _load_raw(path: Path) -> dict:
    if path.exists():
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    return {}
