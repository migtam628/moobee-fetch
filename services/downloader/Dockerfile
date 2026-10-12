FROM debian:bookworm-slim
RUN apt-get update && apt-get install -y --no-install-recommends aria2 ca-certificates && \
    rm -rf /var/lib/apt/lists/* && mkdir /downloads && chown 10001:10001 /downloads
USER 10001:10001
WORKDIR /downloads
