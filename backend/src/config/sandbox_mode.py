from pathlib import Path
from typing import Literal

import yaml

SandboxMode = Literal["local", "aio", "provisioner"]


def detect_sandbox_mode(config_file: str | Path | None) -> SandboxMode:
    """Detect the effective sandbox runtime from config.yaml."""
    if config_file is None:
        return "local"

    config_path = Path(config_file)
    if not config_path.is_file():
        return "local"

    with config_path.open(encoding="utf-8") as f:
        config_data = yaml.safe_load(f) or {}

    if not isinstance(config_data, dict):
        return "local"

    sandbox_config = config_data.get("sandbox")
    if not isinstance(sandbox_config, dict):
        return "local"

    sandbox_use = sandbox_config.get("use")
    if not isinstance(sandbox_use, str):
        return "local"

    if "src.sandbox.local:LocalSandboxProvider" in sandbox_use:
        return "local"

    if "src.community.aio_sandbox:AioSandboxProvider" not in sandbox_use:
        return "local"

    provisioner_url = sandbox_config.get("provisioner_url")
    if isinstance(provisioner_url, str) and provisioner_url.strip():
        return "provisioner"

    return "aio"
