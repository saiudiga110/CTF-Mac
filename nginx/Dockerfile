FROM nginxproxy/nginx-proxy:latest

# Install openssl for certificate generation
RUN apt-get update && apt-get install -y openssl && rm -rf /var/lib/apt/lists/*

# Copy certificate generation script
COPY ./cert-entrypoint.sh /app/cert-entrypoint.sh
RUN chmod +x /app/cert-entrypoint.sh

COPY ./nginx.conf /etc/nginx/nginx.conf
COPY ./proxy-settings.conf /etc/nginx/conf.d/proxy-settings.conf

# Create certs directory
RUN mkdir -p /etc/nginx/certs

# Override entrypoint to generate certs first
ENTRYPOINT ["/app/cert-entrypoint.sh"]
CMD ["forego", "start", "-r"]
