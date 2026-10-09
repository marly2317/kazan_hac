# syntax=docker/dockerfile:1

FROM node:22-alpine AS build

WORKDIR /app

# `npm ci` needs the lockfile; both are copied first so a source-only edit reuses this layer.
COPY package.json package-lock.json* ./
RUN npm ci

COPY . .
RUN npm run build

# The runtime image carries the static bundle only — no Node, no node_modules.
FROM nginx:1.29-alpine AS runtime

COPY docker/static.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/dist /usr/share/nginx/html

EXPOSE 80
