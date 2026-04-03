import os
from pathlib import Path

from .parser import parse_skill_file
from .types import Skill


def get_skills_root_path() -> Path:
    """
    Get the root path of the skills directory.

    Returns:
        Path to the skills directory (deer-flow/skills)
    """
    # backend directory is current file's parent's parent's parent
    backend_dir = Path(__file__).resolve().parent.parent.parent
    # skills directory is sibling to backend directory
    skills_dir = backend_dir.parent / "skills"
    return skills_dir


def load_skills(skills_path: Path | None = None, use_config: bool = True, enabled_only: bool = False, tenant_id: str | None = None) -> list[Skill]:
    """
    Load all skills from the skills directory.

    Scans platform public skills, then appends tenant-specific custom skills
    when *tenant_id* is provided.  The enabled state is determined by
    ``extensions_config.json``.

    Args:
        skills_path: Optional custom path to skills directory.
                     If not provided and use_config is True, uses path from config.
                     Otherwise defaults to deer-flow/skills
        use_config: Whether to load skills path from config (default: True)
        enabled_only: If True, only return enabled skills (default: False)
        tenant_id: If provided (and not "default"), also loads tenant-scoped skills
                   from ``tenants/{tenant_id}/skills/``.

    Returns:
        List of Skill objects, sorted by name
    """
    if skills_path is None:
        if use_config:
            try:
                from src.config import get_app_config

                config = get_app_config()
                skills_path = config.skills.get_skills_path()
            except Exception:
                # Fallback to default if config fails
                skills_path = get_skills_root_path()
        else:
            skills_path = get_skills_root_path()

    if not skills_path.exists():
        return []

    skills = []

    # Scan public and custom directories
    for category in ["public", "custom"]:
        category_path = skills_path / category
        if not category_path.exists() or not category_path.is_dir():
            continue

        for current_root, dir_names, file_names in os.walk(category_path):
            # Keep traversal deterministic and skip hidden directories.
            dir_names[:] = sorted(name for name in dir_names if not name.startswith("."))
            if "SKILL.md" not in file_names:
                continue

            skill_file = Path(current_root) / "SKILL.md"
            relative_path = skill_file.parent.relative_to(category_path)

            skill = parse_skill_file(skill_file, category=category, relative_path=relative_path)
            if skill:
                skills.append(skill)

    # Append tenant-scoped custom skills when tenant_id is provided.
    # Tenant skills with the same name as platform skills override them.
    if tenant_id and tenant_id != "default":
        try:
            from src.config.paths import get_paths

            tenant_skills_dir = get_paths().tenant_dir(tenant_id) / "skills"
            if tenant_skills_dir.exists() and tenant_skills_dir.is_dir():
                tenant_skills: list[Skill] = []
                for current_root, dir_names, file_names in os.walk(tenant_skills_dir):
                    dir_names[:] = sorted(name for name in dir_names if not name.startswith("."))
                    if "SKILL.md" not in file_names:
                        continue

                    skill_file = Path(current_root) / "SKILL.md"
                    relative_path = skill_file.parent.relative_to(tenant_skills_dir)

                    skill = parse_skill_file(skill_file, category="custom", relative_path=relative_path)
                    if skill:
                        tenant_skills.append(skill)

                # Remove platform skills that are overridden by tenant skills
                if tenant_skills:
                    tenant_skill_names = {s.name for s in tenant_skills}
                    skills = [s for s in skills if s.name not in tenant_skill_names]
                    skills.extend(tenant_skills)
        except Exception as e:
            print(f"Warning: Failed to load tenant skills for {tenant_id}: {e}")

    # Load skills state configuration and update enabled status
    # NOTE: We use ExtensionsConfig.from_file() instead of get_extensions_config()
    # to always read the latest configuration from disk. This ensures that changes
    # made through the Gateway API (which runs in a separate process) are immediately
    # reflected in the LangGraph Server when loading skills.
    try:
        from src.config.extensions_config import ExtensionsConfig

        extensions_config = ExtensionsConfig.from_tenant(tenant_id)
        for skill in skills:
            skill.enabled = extensions_config.is_skill_enabled(skill.name, skill.category)
    except Exception as e:
        # If config loading fails, default to all enabled
        print(f"Warning: Failed to load extensions config: {e}")

    # Filter by enabled status if requested
    if enabled_only:
        skills = [skill for skill in skills if skill.enabled]

    # Sort by name for consistent ordering
    skills.sort(key=lambda s: s.name)

    return skills
