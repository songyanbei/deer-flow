import pytest

from src.sandbox.local.local_sandbox import LocalSandbox
from src.sandbox.local.local_sandbox_provider import LocalSandboxProvider


def test_local_sandbox_provider_blocks_when_oidc_enabled(monkeypatch):
    """LocalSandboxProvider must refuse to acquire when OIDC is enabled."""
    monkeypatch.setenv("OIDC_ENABLED", "true")
    provider = LocalSandboxProvider()
    with pytest.raises(RuntimeError, match="cannot be used when OIDC is enabled"):
        provider.acquire("thread-1")


def test_local_sandbox_provider_allows_when_oidc_disabled(monkeypatch):
    """LocalSandboxProvider works normally when OIDC is disabled."""
    monkeypatch.setenv("OIDC_ENABLED", "false")
    provider = LocalSandboxProvider()
    sandbox_id = provider.acquire("thread-1")
    assert sandbox_id == "local"


def test_windows_shell_command_prefers_pwsh(monkeypatch):
    def fake_which(name: str) -> str | None:
        shells = {
            "pwsh": r"C:\Program Files\PowerShell\7\pwsh.exe",
            "powershell": r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe",
            "cmd": r"C:\Windows\System32\cmd.exe",
        }
        return shells.get(name)

    monkeypatch.setattr("src.sandbox.local.local_sandbox.shutil.which", fake_which)

    assert LocalSandbox._get_windows_shell_command("echo test") == [
        r"C:\Program Files\PowerShell\7\pwsh.exe",
        "-NoProfile",
        "-NonInteractive",
        "-Command",
        "echo test",
    ]


def test_windows_shell_command_falls_back_to_cmd(monkeypatch):
    def fake_which(name: str) -> str | None:
        shells = {
            "pwsh": None,
            "powershell": None,
            "cmd": r"C:\Windows\System32\cmd.exe",
        }
        return shells.get(name)

    monkeypatch.setattr("src.sandbox.local.local_sandbox.shutil.which", fake_which)

    assert LocalSandbox._get_windows_shell_command("echo test") == [
        r"C:\Windows\System32\cmd.exe",
        "/d",
        "/s",
        "/c",
        "echo test",
    ]
