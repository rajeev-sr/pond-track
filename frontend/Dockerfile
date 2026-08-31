# Frontend: build the React/Vite bundle, then serve it from nginx.
#
# Multi-stage so the runtime image carries no Node toolchain -- the final layer
# is nginx plus static assets, a few tens of megabytes rather than several
# hundred.

FROM node:22-alpine AS build
WORKDIR /app

# Dependencies first: editing source must not invalidate the npm layer.
COPY package.json package-lock.json* ./
RUN npm ci --no-audit --no-fund 2>/dev/null || npm install --no-audit --no-fund

COPY tsconfig.json vite.config.ts index.html ./
COPY src ./src
# `npm run build` typechecks before bundling, so a type error fails the image
# build rather than shipping.
RUN npm run build

FROM nginx:1.27-alpine AS runtime
COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/dist /usr/share/nginx/html

EXPOSE 80
# 127.0.0.1, not localhost: nginx's entrypoint only patches in `listen [::]:80`
# when default.conf is the one it shipped, and ours is not -- so nginx binds
# IPv4 only, while `localhost` inside the container resolves to ::1 first. The
# container then reports unhealthy forever while serving traffic perfectly.
HEALTHCHECK --interval=10s --timeout=3s --retries=3 --start-period=5s \
    CMD wget -qO- http://127.0.0.1/ >/dev/null || exit 1
