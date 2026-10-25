#!/usr/bin/env python3
"""Validate one operator-allowlisted numeric unicast bind; never widen input.

Shared contract with configuration/opencode-bind.ps1; both reject IPv4-mapped
IPv6 addresses (dotted or hex) and accept only canonical numeric unicast forms,
verified by the shared parity table in tests/test_opencode_auth_bind.py.
"""
import ipaddress
import sys


def valid(value):
    try:
        address = ipaddress.ip_address(value)
    except ValueError:
        return False
    # Both reject IPv4-mapped IPv6 addresses (fail-closed, simplest and fail-safe).
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped:
        return False
    # The .NET twin parses short forms ("127.1") and bracketed text ("[::1]")
    # that this rejects at parse time; both accept only the canonical spelling,
    # so neither validator can be talked past its own table.
    if not isinstance(value, str) or str(address).lower() != value.lower():
        return False
    if isinstance(address, ipaddress.IPv6Address):
        return not (address.is_unspecified or address.is_multicast)
    # IPv4: the whole 0.0.0.0/8 (first octet 0, wildcard included) and the
    # 224/4 multicast + 240/4 reserved ranges are not host unicast - the same
    # byte rule the PowerShell twin applies.
    first = address.packed[0]
    return first != 0 and first < 224


if __name__ == '__main__':
    sys.exit(0 if len(sys.argv) == 2 and valid(sys.argv[1]) else 1)
