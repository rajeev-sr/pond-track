# Frontend container.
#
# M0 ships the nginx layer only -- it reverse-proxies the API, TiTiler and the
# WebSocket stream, so the deployment topology in HLD 9 is real and testable
# from the first phase. The React/Vite build stage is added in M2-7.
FROM nginx:1.27-alpine

COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY public/ /usr/share/nginx/html/

EXPOSE 80
HEALTHCHECK --interval=30s --timeout=3s --retries=3 \
    CMD wget -qO- http://localhost/ >/dev/null || exit 1
