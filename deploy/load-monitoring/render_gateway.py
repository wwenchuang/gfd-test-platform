#!/usr/bin/env python3
"""Render a restricted, private HTTP gateway; never installs or starts services."""
import argparse
import ipaddress


def private_ipv4(value):
    address = ipaddress.ip_address(value)
    if not any(address in ipaddress.ip_network(net) for net in ('10.0.0.0/8', '172.16.0.0/12', '192.168.0.0/16')):
        raise argparse.ArgumentTypeError('必须填写实际 RFC1918 内网 IPv4 地址')
    return str(address)


parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--listen-ip', required=True, type=private_ipv4)
parser.add_argument('--backend-ip', required=True, type=private_ipv4)
args = parser.parse_args()
print('''# Install in nginx http context after reviewing the actual private addresses.
server {
    listen %s:9091;
    server_name _;
    allow %s;
    deny all;
    client_max_body_size 1k;
    access_log off;
    location = /api/v1/query_range {
        limit_except GET { deny all; }
        proxy_pass http://127.0.0.1:9090;
        proxy_connect_timeout 2s;
        proxy_read_timeout 10s;
        proxy_send_timeout 2s;
        proxy_set_header Authorization "";
    }
    location / { return 404; }
}''' % (args.listen_ip, args.backend_ip))
