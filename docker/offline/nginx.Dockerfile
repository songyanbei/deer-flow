FROM nginx:alpine

COPY docker/nginx/nginx.offline.conf /etc/nginx/nginx.conf

EXPOSE 2026
