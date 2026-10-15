# Linux amd64. Update both digests and the snapshot together after validation.
FROM rust:1.89.0-bookworm@sha256:c9ac3fa8945b61dede1e4500d25028aa8fd8a8fe46365fcf9c0422f8d999b9b0 AS build
RUN rm -f /etc/apt/sources.list.d/* && echo 'deb [check-valid-until=no] http://snapshot.debian.org/archive/debian/20261001T000000Z bookworm main' > /etc/apt/sources.list && apt-get update && apt-get install -y --no-install-recommends python3 && rm -rf /var/lib/apt/lists/*
WORKDIR /src/gitpickle
COPY . .
ENV SOURCE_DATE_EPOCH=0
RUN python3 tools/gitpickle_package.py --output /out

FROM debian:bookworm-slim@sha256:3783cc01769c7b2b1b83a5c5ad96c815348e28ed7da68e2e3687004faa906251 AS runtime
RUN rm -f /etc/apt/sources.list.d/* && echo 'deb [check-valid-until=no] http://snapshot.debian.org/archive/debian/20261001T000000Z bookworm main' > /etc/apt/sources.list && apt-get update && apt-get install -y --no-install-recommends python3 git git-lfs util-linux ca-certificates && rm -rf /var/lib/apt/lists/*
RUN groupadd --gid 10001 gitpickle && useradd --uid 10001 --gid 10001 --home-dir /var/lib/gitpickle gitpickle && install -d -m 0700 -o 10001 -g 10001 /var/lib/gitpickle /var/lib/gitpickle/restores
COPY --from=build /out/gitpickle /usr/local/bin/gitpickle
COPY --from=build /out/NOTICES.txt /out/sbom.cdx.json /usr/share/gitpickle/
COPY --from=build /out/licenses /usr/share/gitpickle/licenses
COPY tools/gitpickle_runtime_sbom.py /tmp/runtime-sbom.py
# Retain Debian copyright notices and the complete installed package inventory.
RUN python3 /tmp/runtime-sbom.py /usr/share/gitpickle/sbom.cdx.json && rm /tmp/runtime-sbom.py
USER 10001:10001
WORKDIR /var/lib/gitpickle
ENV PYTHONDONTWRITEBYTECODE=1
EXPOSE 8080
STOPSIGNAL SIGTERM
HEALTHCHECK --interval=10s --timeout=3s --start-period=60s --retries=3 CMD ["python3", "-I", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/readyz', timeout=2).read()"]
ENTRYPOINT ["/usr/local/bin/gitpickle", "serve"]
CMD ["--state", "/var/lib/gitpickle/archive", "--policy", "/run/gitpickle/policy.json", "--host", "0.0.0.0", "--container-network", "--port", "8080", "--staging-bytes", "1073741824"]
