# syntax=docker/dockerfile:1

FROM node:22-alpine AS build

WORKDIR /app

# `npm ci` needs the lockfile; both are copied first so a source-only edit reuses this layer.
COPY package.json package-lock.json* ./
RUN npm ci

COPY . .

# Демонстрационная учётная запись для ссылки под формой входа. Пусто по умолчанию — ссылки
# тогда нет вовсе. Compose подставляет сюда те же значения, которыми создаётся сам
# пользователь, так что ссылка не может пережить выключение `SEED_DEMO_USER`.
#
# Значение попадает в собранный бандл и видно любому посетителю. Это приемлемо ровно для
# стенда с общеизвестным демо-аккаунтом и неприемлемо где-либо ещё.
ARG VITE_DEMO_EMAIL=
ARG VITE_DEMO_PASSWORD=
ENV VITE_DEMO_EMAIL=$VITE_DEMO_EMAIL \
    VITE_DEMO_PASSWORD=$VITE_DEMO_PASSWORD

RUN npm run build

# The runtime image carries the static bundle only — no Node, no node_modules.
FROM nginx:1.29-alpine AS runtime

COPY docker/static.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/dist /usr/share/nginx/html

EXPOSE 80
