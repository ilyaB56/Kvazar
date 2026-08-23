FROM node:20-alpine AS build
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci
COPY . .
RUN npm run build

FROM nginx:1.27-alpine
COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY nginx-tls.conf /etc/nginx/nginx-tls.conf
COPY --from=build /app/dist /usr/share/nginx/html
# WEB_TLS=1 — включить TLS-вариант конфига (сертификаты в /etc/nginx/certs)
CMD ["/bin/sh", "-c", "if [ \"$WEB_TLS\" = \"1\" ]; then cp /etc/nginx/nginx-tls.conf /etc/nginx/conf.d/default.conf; fi && exec nginx -g 'daemon off;'"]
EXPOSE 80 443
