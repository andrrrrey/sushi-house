FROM ubuntu:24.04

RUN apt-get update \
    && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends asterisk gettext-base ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && if getent group 101 >/dev/null; then runtime_group="$(getent group 101 | cut -d: -f1)"; else groupadd --gid 101 app-runtime && runtime_group=app-runtime; fi \
    && usermod -aG "$runtime_group" asterisk

COPY config/ /opt/asterisk-config/
COPY entrypoint.sh /usr/local/bin/asterisk-entrypoint

RUN chmod 0755 /usr/local/bin/asterisk-entrypoint

EXPOSE 5060/udp 5038/tcp 10000-10100/udp

ENTRYPOINT ["/usr/local/bin/asterisk-entrypoint"]
