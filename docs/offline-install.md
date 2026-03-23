# DeerFlow Offline Install

This project now includes an offline Docker deployment flow for Linux `x86_64` servers.

## Build The Offline Bundle On Windows

Use Docker Desktop in Linux container mode, then run:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\build-offline-bundle.ps1
```

The script builds three Linux images and packages them into `dist/offline/deer-flow-offline-linux-amd64.zip`.

## Build The Offline Bundle On Linux

On a temporary Linux machine with internet access and Docker:

```bash
chmod +x ./scripts/build-offline-bundle.sh
./scripts/build-offline-bundle.sh
```

The Linux build script packages the bundle as:

```text
dist/offline/deer-flow-offline-linux-amd64.tar.gz
```

## Install On The Linux Server

Extract the bundle on the target server, then run:

```bash
tar -xzf deer-flow-offline-linux-amd64.tar.gz
cd deer-flow-offline-linux-amd64
chmod +x install.sh
./install.sh \
  --model-base-url http://your-qwen-gateway/v1 \
  --model-api-key your-api-key \
  --model-name your-qwen-model \
  --install-dir /opt/deer-flow \
  --port 2026
```

If a model argument is omitted, `install.sh` will prompt for it interactively.

## What The Installer Does

- Loads the offline Docker images from `images/*.tar`
- Writes runtime files under `/opt/deer-flow/runtime`
- Generates a minimal `config.yaml` for an internal OpenAI-compatible Qwen API
- Starts `frontend`, `gateway`, `langgraph`, and `nginx` with `docker compose`

## Runtime Commands

After installation:

```bash
/opt/deer-flow/bin/up.sh
/opt/deer-flow/bin/down.sh
/opt/deer-flow/bin/logs.sh
```

## Notes

- The generated config keeps file and bash tools enabled, and disables external web tools by default.
- The default public port is `2026`.
- The installer currently targets Linux `x86_64` servers.
