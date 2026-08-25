FROM node:20-alpine AS build
WORKDIR /src
COPY frontend/package.json frontend/package-lock.json* ./
RUN npm install --no-audit --no-fund
COPY frontend/ ./
RUN npm run build

FROM nginx:1.27-alpine
# The two things that differ between running this locally and running it on a
# host: which port to listen on, and where the API lives. Both are read at
# container start from nginx.conf.template, so one image serves both. The
# defaults below are exactly the docker-compose layout.
ENV PORT=80 \
    BACKEND_ORIGIN=http://backend:8000
COPY frontend/nginx.conf.template /etc/nginx/templates/default.conf.template
COPY frontend/docker-entrypoint.d/ /docker-entrypoint.d/
COPY --from=build /src/dist /usr/share/nginx/html
EXPOSE 80
HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
    CMD wget -q --spider "http://127.0.0.1:$PORT/" || exit 1
