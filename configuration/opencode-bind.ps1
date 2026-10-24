# Numeric unicast only. Naming AUTOOS_OPENCODE_HOST is the operator allowlist.
function Get-AutoOSOpencodeHost {
    $value = '127.0.0.1'
    if (Test-Path Env:AUTOOS_OPENCODE_HOST) { $value = $env:AUTOOS_OPENCODE_HOST }
    $address = $null
    if (-not [System.Net.IPAddress]::TryParse($value, [ref]$address)) {
        throw 'Invalid opencode bind address'
    }
    if ($address.IsIPv4MappedToIPv6) { $address = $address.MapToIPv4() }
    $bytes = $address.GetAddressBytes()
    if ($address.Equals([System.Net.IPAddress]::Any) -or
        $address.Equals([System.Net.IPAddress]::IPv6Any) -or
        $address.IsIPv6Multicast -or
        ($bytes.Length -eq 4 -and ($bytes[0] -ge 224 -or $bytes[0] -eq 0))) {
        throw 'Refusing wildcard or non-unicast opencode bind'
    }
    return $address.ToString()
}
