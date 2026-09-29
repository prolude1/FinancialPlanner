FROM node:22-alpine AS frontend-build
WORKDIR /build
COPY package.json package-lock.json ./
RUN npm ci
COPY telegram-link-entry.js telegram-link-ui.js dashboard-auth-entry.js dashboard-auth.js ./
RUN npm run build

FROM nginxinc/nginx-unprivileged:1.30.5-alpine3.24
USER root
RUN apk upgrade --no-cache
USER 101
ENV NGINX_ENVSUBST_FILTER="^(KEYCLOAK_ORIGIN|KEYCLOAK_ISSUER|KEYCLOAK_REALM|KEYCLOAK_WEB_CLIENT_ID)$"
COPY nginx.conf.template /etc/nginx/templates/default.conf.template
COPY index.html telegram-link.html app.js style.css telegram-link.css /usr/share/nginx/html/
COPY core/ /usr/share/nginx/html/core/
COPY features/ /usr/share/nginx/html/features/
COPY --from=frontend-build /build/dist/telegram-link.bundle.js /usr/share/nginx/html/dist/telegram-link.bundle.js
COPY --from=frontend-build /build/dist/dashboard-auth.bundle.js /usr/share/nginx/html/dist/dashboard-auth.bundle.js
