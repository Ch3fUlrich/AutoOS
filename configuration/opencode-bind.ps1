# Numeric unicast only. Naming AUTOOS_OPENCODE_HOST is the operator allowlist.
# Shared contract with configuration/opencode-bind.py; both reject IPv4-mapped
# IPv6 addresses (dotted or hex) and accept only canonical numeric unicast forms,
# verified by the shared parity table in tests/test_opencode_auth_bind.py.
function Get-AutoOSOpencodeHost {
    $value = '127.0.0.1'
    if (Test-Path Env:AUTOOS_OPENCODE_HOST) { $value = $env:AUTOOS_OPENCODE_HOST }
    $address = $null
    if ((-not [System.Net.IPAddress]::TryParse($value, [ref]$address)) -or
        (-not [string]::Equals($address.ToString(), $value, [System.StringComparison]::OrdinalIgnoreCase))) {
        # TryParse alone is wider than the python twin: it accepts short forms
        # ("127.1" -> 127.0.0.1) and bracketed text ("[::1]"), so the canonical
        # spelling has to match before the address counts as named at all.
        throw 'Invalid opencode bind address'
    }
    if ($address.IsIPv4MappedToIPv6) {
        throw 'Refusing IPv4-mapped IPv6 opencode bind'
    }
    $bytes = $address.GetAddressBytes()
    if ($address.Equals([System.Net.IPAddress]::Any) -or
        $address.Equals([System.Net.IPAddress]::IPv6Any) -or
        $address.IsIPv6Multicast -or
        ($bytes.Length -eq 4 -and ($bytes[0] -ge 224 -or $bytes[0] -eq 0))) {
        throw 'Refusing wildcard or non-unicast opencode bind'
    }
    return $address.ToString()
}
