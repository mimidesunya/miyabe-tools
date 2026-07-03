FROM node:22-bookworm-slim

WORKDIR /app

COPY docker/mcp/package*.json ./
RUN npm ci --include=dev

COPY docker/mcp/tsconfig.json ./
COPY docker/mcp/src ./src
RUN npm run build

ENV NODE_ENV=production
ENV PORT=3000

EXPOSE 3000

CMD ["npm", "start"]
