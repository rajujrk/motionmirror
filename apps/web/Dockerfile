# Built from the repository root so the pnpm workspace and contracts are available.
FROM node:26.13-alpine AS build
RUN npm install --global pnpm@10.14.0
WORKDIR /repo
COPY package.json pnpm-workspace.yaml pnpm-lock.yaml ./
COPY apps/web/package.json apps/web/
COPY packages/contracts/package.json packages/contracts/
COPY tests/e2e/package.json tests/e2e/
RUN pnpm install --frozen-lockfile --filter @motionmirror/web...
COPY packages/contracts packages/contracts
COPY apps/web apps/web
RUN pnpm --filter @motionmirror/web build

FROM node:26.13-alpine
ENV NODE_ENV=production HOSTNAME=0.0.0.0 PORT=3000 NEXT_TELEMETRY_DISABLED=1
RUN addgroup -S web && adduser -S web -G web
WORKDIR /app
COPY --from=build /repo/apps/web/.next/standalone ./
COPY --from=build /repo/apps/web/.next/static ./apps/web/.next/static
USER web
EXPOSE 3000
CMD ["node", "apps/web/server.js"]
