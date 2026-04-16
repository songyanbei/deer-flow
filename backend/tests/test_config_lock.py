"""Tests for the per-tenant config lock and atomic write utilities."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest
import yaml

from src.config._config_lock import atomic_write_json, atomic_write_yaml, tenant_config_lock


class TestAtomicWriteJson:
    def test_basic_write(self, tmp_path: Path):
        target = tmp_path / "config.json"
        data = {"mcpServers": {"github": {"enabled": True}}}
        atomic_write_json(target, data)

        assert target.exists()
        loaded = json.loads(target.read_text(encoding="utf-8"))
        assert loaded == data

    def test_creates_parent_dirs(self, tmp_path: Path):
        target = tmp_path / "a" / "b" / "config.json"
        atomic_write_json(target, {"key": "val"})
        assert target.exists()

    def test_no_leftover_tmp_on_success(self, tmp_path: Path):
        target = tmp_path / "config.json"
        atomic_write_json(target, {"x": 1})
        tmp_files = [f for f in tmp_path.iterdir() if f.suffix == ".tmp"]
        assert tmp_files == []

    def test_overwrites_existing(self, tmp_path: Path):
        target = tmp_path / "config.json"
        atomic_write_json(target, {"v": 1})
        atomic_write_json(target, {"v": 2})
        assert json.loads(target.read_text())["v"] == 2


class TestAtomicWriteYaml:
    def test_basic_write(self, tmp_path: Path):
        target = tmp_path / "config.yaml"
        data = {"name": "test-agent", "description": "hello"}
        atomic_write_yaml(target, data)

        loaded = yaml.safe_load(target.read_text(encoding="utf-8"))
        assert loaded == data


class TestTenantConfigLock:
    def test_same_tenant_serialized(self, tmp_path: Path):
        """Two coroutines on the same tenant+kind must serialize."""
        order: list[str] = []

        async def writer(label: str):
            async with tenant_config_lock("t1", "mcp"):
                order.append(f"{label}_start")
                await asyncio.sleep(0.05)
                order.append(f"{label}_end")

        async def run():
            await asyncio.gather(writer("A"), writer("B"))

        asyncio.run(run())
        assert order[0].endswith("_start")
        assert order[1].endswith("_end")

    def test_different_tenants_concurrent(self, tmp_path: Path):
        """Different tenants must NOT block each other."""
        started: list[str] = []

        async def writer(tid: str):
            async with tenant_config_lock(tid, "mcp"):
                started.append(tid)
                await asyncio.sleep(0.05)

        async def run():
            await asyncio.gather(writer("t1"), writer("t2"))

        asyncio.run(run())
        assert set(started) == {"t1", "t2"}

    def test_with_lockfile(self, tmp_path: Path):
        lockfile = tmp_path / ".test.lock"

        async def run():
            async with tenant_config_lock("default", "mcp", lockfile=lockfile):
                assert lockfile.exists()

        asyncio.run(run())
