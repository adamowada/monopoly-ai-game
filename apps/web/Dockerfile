FROM node:24-slim

ENV PNPM_HOME="/pnpm" \
    PATH="/pnpm:${PATH}" \
    NEXT_TELEMETRY_DISABLED=1

WORKDIR /workspace

RUN corepack enable && corepack prepare pnpm@11.7.0 --activate

COPY package.json pnpm-lock.yaml pnpm-workspace.yaml ./
COPY apps/web/package.json apps/web/package.json
COPY services/api/package.json services/api/package.json
COPY packages/schemas/package.json packages/schemas/package.json

RUN pnpm install --frozen-lockfile

COPY apps/web apps/web
COPY packages/schemas packages/schemas

RUN pnpm --filter @monopoly-ai-game/web exec next build

EXPOSE 3000

ENV HOSTNAME=0.0.0.0 \
    PORT=3000

CMD ["sh", "-c", "pnpm --filter @monopoly-ai-game/web exec next start --hostname ${HOSTNAME:-0.0.0.0} --port ${PORT:-3000}"]
