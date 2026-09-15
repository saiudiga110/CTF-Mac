FROM alpine:latest
RUN apk add --no-cache dnsmasq
EXPOSE 53/udp 53/tcp
ENTRYPOINT ["dnsmasq"]
CMD ["--no-daemon", "--log-facility=-"]
