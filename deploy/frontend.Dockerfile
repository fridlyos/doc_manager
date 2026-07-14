# syntax=docker/dockerfile:1
# Frontend build. Produces static assets. In production these are served by the
# API container; this image is used for the Vite dev server (ui profile) and to
# emit a build artifact. No CDN or hosted-font access at runtime.
FROM node:22-slim AS build
WORKDIR /app
COPY frontend/package.json frontend/package-lock.json* ./
RUN npm ci || npm install
COPY frontend/ ./
RUN npm run build

# Dev-server stage (ui profile). Production serves ./dist via the API.
FROM node:22-slim AS dev
WORKDIR /app
COPY frontend/package.json frontend/package-lock.json* ./
RUN npm ci || npm install
COPY frontend/ ./
EXPOSE 5173
CMD ["npm", "run", "dev", "--", "--host", "0.0.0.0"]
