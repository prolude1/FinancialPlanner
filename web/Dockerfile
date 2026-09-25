FROM nginxinc/nginx-unprivileged:1.30.5-alpine3.24
USER root
RUN apk upgrade --no-cache
USER 101
COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY index.html app.js data-management.js stocks.js calculator.js cashflow.js activity.js style.css data-management.css stocks.css calculator.css activity.css /usr/share/nginx/html/
