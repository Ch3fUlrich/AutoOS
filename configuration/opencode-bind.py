#!/usr/bin/env python3
"""Validate one operator-allowlisted numeric unicast bind; never widen input."""
import ipaddress
import sys


def valid(value):
    try:
        address = ipaddress.ip_address(value)
    except ValueError:
        return False
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped:
        address = address.ipv4_mapped
    return not (address.is_unspecified or address.is_multicast
                or str(address) == '255.255.255.255')


if __name__ == '__main__':
    sys.exit(0 if len(sys.argv) == 2 and valid(sys.argv[1]) else 1)
