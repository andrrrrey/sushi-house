#!/bin/sh
set -eu

: "${ASTERISK_AMI_SECRET:?ASTERISK_AMI_SECRET is required}"
: "${PUBLIC_IP:?PUBLIC_IP is required}"

install -d -o 100 -g 101 -m 0770 /runtime/asterisk
if [ ! -e /runtime/asterisk/pjsip_mango.conf ]; then
  install -o 100 -g 101 -m 0640 /dev/null /runtime/asterisk/pjsip_mango.conf
fi

envsubst '${PUBLIC_IP}' < /opt/asterisk-config/pjsip.conf.template > /etc/asterisk/pjsip.conf
envsubst '${ASTERISK_AMI_SECRET}' < /opt/asterisk-config/manager.conf.template > /etc/asterisk/manager.conf
install -m 0644 /opt/asterisk-config/extensions.conf /etc/asterisk/extensions.conf
install -m 0644 /opt/asterisk-config/rtp.conf /etc/asterisk/rtp.conf
install -m 0644 /opt/asterisk-config/logger.conf /etc/asterisk/logger.conf
install -m 0644 /opt/asterisk-config/modules.conf /etc/asterisk/modules.conf

exec asterisk -f -U asterisk -G asterisk -vvv
