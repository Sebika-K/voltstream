# Two stages: build the site with Node, then throw Node away and ship only the
# finished static files inside a tiny web server image.

# ---- Stage 1: build ----
FROM node:22-alpine AS build
WORKDIR /app

# Dependencies first so they're cached unless package files change.
COPY package.json package-lock.json ./
RUN npm ci

COPY . .

# Where the *browser* will find the backend API. Baked into the built files.
ARG VITE_API_BASE_URL=http://localhost:8000
ENV VITE_API_BASE_URL=$VITE_API_BASE_URL

RUN npm run build

# ---- Stage 2: serve ----
FROM nginx:alpine
COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/dist /usr/share/nginx/html
EXPOSE 80
