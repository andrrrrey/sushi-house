#!/bin/sh
set -eu

CHAIN=SUSHI_SIP

iptables -N "$CHAIN" 2>/dev/null || true
iptables -F "$CHAIN"
iptables -A "$CHAIN" -p udp --dport 5060 -s 81.88.86.0/24 -j RETURN
iptables -A "$CHAIN" -p udp --dport 5060 -j DROP
iptables -A "$CHAIN" -p udp --dport 10000:10100 -s 81.88.88.0/23 -j RETURN
iptables -A "$CHAIN" -p udp --dport 10000:10100 -j DROP
iptables -A "$CHAIN" -j RETURN

iptables -C DOCKER-USER -j "$CHAIN" 2>/dev/null || iptables -I DOCKER-USER 1 -j "$CHAIN"
