FROM node:22-alpine

ENV NODE_ENV=production \
    PORT=3000 \
    HOSTNAME=0.0.0.0 \
    SKIP_ENV_VALIDATION=1

WORKDIR /app

RUN if [ -f /etc/apk/repositories ]; then \
        sed -i 's|http://|https://|g' /etc/apk/repositories; \
    fi

COPY frontend ./frontend

RUN corepack enable \
    && corepack install -g pnpm@10.26.2 \
    && cd /app/frontend \
    && pnpm install --frozen-lockfile \
    && pnpm run build

WORKDIR /app/frontend

EXPOSE 3000

CMD ["node", "scripts/run-next-with-root-env.mjs", "start", "-H", "0.0.0.0", "-p", "3000"]
