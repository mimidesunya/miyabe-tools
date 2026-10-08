# ビルドの段で TypeScript を dist へ書き出し、実行の段には本番の依存と dist だけを置く。
# 以前は src を bind mount して起動のたびに tsc を走らせ、devDependencies も本番に残していた。
# コードを変えたら `docker compose up -d --build mcp` でイメージを作り直す（deploy.py が行う）。
FROM node:22-bookworm-slim AS build

WORKDIR /app

COPY docker/mcp/package*.json ./
RUN npm ci --include=dev

COPY docker/mcp/tsconfig.json ./
COPY docker/mcp/src ./src
RUN npm run build

FROM node:22-bookworm-slim

WORKDIR /app

ENV NODE_ENV=production
ENV PORT=3000

COPY docker/mcp/package*.json ./
RUN npm ci --omit=dev && npm cache clean --force

COPY --from=build /app/dist ./dist

EXPOSE 3000

CMD ["node", "dist/server.js"]
