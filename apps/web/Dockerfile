# The Admin image: Next.js production build, served by the standalone Node server as a non-root user.
# Build from apps/web:
#   docker build -t nemo-k-cards-admin \
#     --build-arg NEXT_PUBLIC_SUPABASE_URL=https://<ref>.supabase.co \
#     --build-arg NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY=sb_publishable_... .
#
# Next inlines NEXT_PUBLIC_* into the browser bundle (and the proxy) at BUILD time, so they are build args:
# - NEXT_PUBLIC_API_URL defaults to "/backend": the browser calls the API on the Admin's own origin and the
#   compose front (Caddy) routes /backend/* to the API. No domain or server is baked into the image, and no CORS.
# - NEXT_PUBLIC_SUPABASE_URL / _PUBLISHABLE_KEY name the Supabase project, so one Admin image per Supabase
#   project (staging, later production); CI passes them from that GitHub environment's variables. Both are
#   public by design (they ship to every browser); never pass a secret or service key here.
# Nothing else is read at build time, and the running Admin gets no env file: it holds no secrets.

FROM node:22-bookworm-slim AS base
ENV COREPACK_ENABLE_DOWNLOAD_PROMPT=0 NEXT_TELEMETRY_DISABLED=1
RUN corepack enable
WORKDIR /app

FROM base AS deps
COPY package.json pnpm-lock.yaml pnpm-workspace.yaml ./
RUN --mount=type=cache,id=pnpm-store,target=/root/.local/share/pnpm/store pnpm install --frozen-lockfile

FROM base AS build
ARG NEXT_PUBLIC_API_URL=/backend
ARG NEXT_PUBLIC_SUPABASE_URL=
ARG NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY=
ENV NEXT_PUBLIC_API_URL=$NEXT_PUBLIC_API_URL \
    NEXT_PUBLIC_SUPABASE_URL=$NEXT_PUBLIC_SUPABASE_URL \
    NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY=$NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY \
    NEXT_OUTPUT=standalone
COPY --from=deps /app/node_modules ./node_modules
COPY . .
RUN pnpm build

FROM node:22-bookworm-slim
ENV NODE_ENV=production NEXT_TELEMETRY_DISABLED=1 PORT=3000 HOSTNAME=0.0.0.0
WORKDIR /app
COPY --from=build --chown=node:node /app/public ./public
COPY --from=build --chown=node:node /app/.next/standalone ./
COPY --from=build --chown=node:node /app/.next/static ./.next/static
USER node
EXPOSE 3000
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD ["node", "-e", "fetch('http://127.0.0.1:3000/sign-in').then(r => process.exit(r.status < 500 ? 0 : 1), () => process.exit(1))"]
CMD ["node", "server.js"]
