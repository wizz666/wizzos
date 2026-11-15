# Caddy med Cloudflare-stöd: hämtar https-certifikat via DNS, så det fungerar
# även när servern bara nås via Tailscale eller hemnätverket.
FROM caddy:2.10-builder AS builder
RUN xcaddy build --with github.com/caddy-dns/cloudflare

FROM caddy:2.10
COPY --from=builder /usr/bin/caddy /usr/bin/caddy
ENV XDG_DATA_HOME=/proxy/data XDG_CONFIG_HOME=/proxy/config
# WizzOS skriver /proxy/Caddyfile och laddar om via en socket i /proxy.
CMD ["sh", "-c", "mkdir -p /proxy && [ -f /proxy/Caddyfile ] || printf '{\\n\\tadmin unix//proxy/admin.sock\\n}\\n' > /proxy/Caddyfile; rm -f /proxy/admin.sock; exec caddy run --config /proxy/Caddyfile --adapter caddyfile"]
