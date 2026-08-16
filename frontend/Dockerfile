FROM node:24-alpine AS build

WORKDIR /app
RUN corepack enable

COPY package.json pnpm-lock.yaml pnpm-workspace.yaml ./
RUN pnpm install --frozen-lockfile

COPY index.html tsconfig.json tsconfig.app.json tsconfig.node.json vite.config.ts ./
COPY src ./src
RUN pnpm build

FROM nginx:1.29-alpine AS runtime

ARG SAGE_RELEASE_COMMIT=unknown

LABEL org.opencontainers.image.revision="${SAGE_RELEASE_COMMIT}"

COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY security-headers.conf /etc/nginx/snippets/security-headers.conf
COPY proxy.conf /etc/nginx/snippets/proxy.conf
COPY --from=build /app/dist /usr/share/nginx/html

EXPOSE 80

HEALTHCHECK --interval=10s --timeout=5s --start-period=10s --retries=6 \
  CMD wget --quiet --tries=1 --spider http://127.0.0.1/api/ready || exit 1

