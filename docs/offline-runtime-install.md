# DeerFlow Offline Runtime Install

This bundle is the non-Docker offline deployment path for Linux `x86_64`.
It now includes an embedded nginx reverse proxy so the offline server exposes a
single public entrypoint instead of requiring direct browser access to `3000`,
`8001`, and `2024`.

## Build On A Temporary Linux Machine

On a Linux machine that can access the internet:

```bash
chmod +x ./scripts/build-offline-runtime.sh
./scripts/build-offline-runtime.sh
```

The script downloads:

- A portable Linux `x86_64` Node.js 22 runtime
- A portable Linux `x86_64` Python 3.12 runtime
- An nginx runtime copied from the build machine (`nginx` must be installed there)
- Backend Python dependencies
- Frontend standalone build output

The final archive is:

```text
dist/offline-runtime/deer-flow-runtime-linux-amd64.tar.gz
```

## Install On The Offline Linux Server

```bash
tar -xzf deer-flow-runtime-linux-amd64.tar.gz
cd deer-flow-runtime-linux-amd64
chmod +x install.sh
./install.sh \
  --install-dir /opt/deer-flow \
  --public-port 2026 \
  --model-base-url http://your-qwen-gateway/v1 \
  --model-api-key your-api-key \
  --model-name your-qwen-model
```

You can also omit the model arguments. The installer will still finish without
prompting for input, write/update `.env`, and skip auto-start until the model
configuration is completed manually.

The installer copies the bundled runtime into the install directory, writes `.env`,
preserves the root-style `config.yaml`, renders the nginx config, and starts the
services only when the required model configuration is present:

- Public entrypoint on `2026` (nginx)
- Internal frontend on `3000`
- Internal gateway on `8001`
- Internal LangGraph on `2024`

## Manage Services

```bash
/opt/deer-flow/bin/up.sh
/opt/deer-flow/bin/down.sh
/opt/deer-flow/bin/logs.sh
```

You can also run the root scripts directly:

```bash
/opt/deer-flow/start-services.sh
/opt/deer-flow/stop-services.sh
```

## Notes

- This path intentionally avoids Docker entirely.
- The frontend is built as a production standalone server, but the runtime layout keeps the same root `.env` and `config.yaml` style as the current project.
- The installer assumes a standard Linux environment with shell utilities such as `sh`, `cp`, `ln`, `mkdir`, and `tar`.
- Browser access should use the nginx entrypoint, for example `http://server-ip:2026`.
