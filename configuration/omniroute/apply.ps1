<#
.SYNOPSIS
  Apply the AutoOS router configuration to OmniRoute (Windows).

.DESCRIPTION
  1. Registers every provider key found in configuration/api-keys.yml: as a
     built-in connection where the installed CLI knows the provider id, and as
     an OpenAI-compatible provider node plus a connection bound to it where it
     does not (a registry endpoint such as meta-api's, which the CLI has nothing
     to attach a key to).
  2. Refreshes the gateway's model catalog for what it just registered
     (omniroute models <provider>), so one run is enough for a fresh machine.
  3. (Re)creates the tier combos from configuration/omniroute/combos.json.
  4. Prunes the combos listed there as "retired" or "omitted" from the store
     (only those).

  Safe to re-run: providers are add-or-update, combos are replaced in place,
  and a managed orphan (retired/omitted) that is already gone is simply not
  found again.
  Model refs the live catalog does not know are skipped with a warning, so a
  renamed upstream model degrades one tier leg instead of breaking the run.

.EXAMPLE
  .\configuration\omniroute\apply.ps1
.EXAMPLE
  .\configuration\omniroute\apply.ps1 -DryRun
.EXAMPLE
  .\configuration\omniroute\apply.ps1 -Probe   # one tiny request per combo
#>
[CmdletBinding()]
param(
    [switch]$DryRun,
    [switch]$Probe
)

Set-StrictMode -Version Latest
# 'Continue', not 'Stop': Windows PowerShell 5.1 promotes native-command
# stderr to a terminating error under Stop, and the omniroute CLI writes a
# harmless .env warning to stderr on every invocation. Outcomes are checked
# explicitly via $LASTEXITCODE / try-catch instead.
$ErrorActionPreference = 'Continue'

$Here      = Split-Path -Parent $MyInvocation.MyCommand.Path
$Root      = Split-Path -Parent (Split-Path -Parent $Here)
# AUTOOS_OMNIROUTE_URL points the run (and the CLI) at another gateway; the
# tests aim it at a closed port so a dry run never reads the live one.
$Gateway   = if ($env:AUTOOS_OMNIROUTE_URL) { $env:AUTOOS_OMNIROUTE_URL } else { 'http://127.0.0.1:20128' }
if ($env:AUTOOS_OMNIROUTE_URL) { $env:OMNIROUTE_BASE_URL = $Gateway }
$KeysFile  = if ($env:AUTOOS_KEYS_FILE) { $env:AUTOOS_KEYS_FILE } else { Join-Path $Root 'configuration\api-keys.yml' }
$CombosFile = Join-Path $Here 'combos.json'
if ($DryRun) { Write-Host 'This is a dry run - nothing is registered, created or started.' }

# No api-keys.yml, no keys to apply. Say how to create it (bash prints the
# same), but stay green under -DryRun so a preview never demands secrets.
$Keys = @{}
$KeysMissing = $false
if (-not (Test-Path $KeysFile)) {
    $KeysMissing = $true
    Write-Host 'No configuration\api-keys.yml yet - copy configuration\api-keys.example.yml and fill it in.'
    Write-Host 'Continuing with combos only.'
} else {
    foreach ($line in (Get-Content $KeysFile -Encoding utf8)) {
        $t = $line.Trim()
        if ($t -eq '' -or $t.StartsWith('#') -or -not $t.Contains(':')) { continue }
        $key = ($t -split ':', 2)[0].Trim().ToLowerInvariant()
        $val = ($t -split ':', 2)[1].Trim().Trim('"').Trim("'")
        # A copied template keeps REPLACE_WITH_* for keys you do not have;
        # registering one would also block the real key later.
        if ($val -like 'REPLACE_WITH_*') { continue }
        if ($key -and $val) { $Keys[$key] = $val }
    }
}

if (-not (Get-Command omniroute -ErrorAction SilentlyContinue)) {
    if ($DryRun) {
        Write-Host 'OmniRoute CLI not installed - dry run continues with the static plan (nothing started, registered or created).'
    } else {
        Write-Host 'OmniRoute CLI not installed. Run: .\setup.ps1 -Only omniroute -Yes'
        exit 1
    }
}

# --- Gateway up? --- ---
function Test-Gateway {
    try { (Invoke-WebRequest -Uri "$Gateway/api/health" -UseBasicParsing -TimeoutSec 5).StatusCode -eq 200 }
    catch { $false }
}
if (-not (Test-Gateway)) {
    if ($DryRun) {
        Write-Host 'Gateway is down; dry run continues with the static plan (would start it with: omniroute --no-open --port 20128).'
    } else {
        Write-Host 'Starting OmniRoute (background)...'
        Start-Process -FilePath 'omniroute' -ArgumentList '--no-open', '--port', '20128' -WindowStyle Hidden
        $tries = 0
        while ((-not (Test-Gateway)) -and ($tries -lt 24)) { Start-Sleep 5; $tries++ }
        if (-not (Test-Gateway)) { Write-Host 'Gateway did not start - run: omniroute doctor'; exit 1 }
    }
}
if (Test-Gateway) { Write-Host "Gateway OK on $Gateway" }

# Provider registry: catalog\ai-registry.json's `providers` section is the
# single source of truth for which api-keys.yml name maps to which OmniRoute
# provider id and for the provider-specific connection data. catalog\providers.json
# is deleted (task A5e); catalog\ai-registry.json is the only provider source.
# apply.sh
# reads the same registry file, so the two scripts cannot drift. Only
# providers with an omniroute_id are registered: meta (unregistered
# 2026-09-23, openrouter-first, no combo leg) and the omniroute client key
# carry none and are skipped, exactly as before. A provider every one of
# whose route legs the registry marks unavailable
# (routes.<id>.unavailable_legs, providers.<id>.available: false) is skipped
# too - registering a connection nothing can ever route to proves nothing
# and just leaves a dead entry.
function Get-AutoOSProviderMap {
    param([string]$CatalogPath)
    $doc = Get-Content $CatalogPath -Raw -Encoding utf8 | ConvertFrom-Json
    $providers = $doc.providers
    $routes = if ($doc.PSObject.Properties['routes']) { $doc.routes } else { $null }

    # A route leg's prefix names a providers key or any provider's
    # omniroute_id (mirrors tools/registry.py's resolve_leg two-step match).
    function Resolve-AutoOSProviderId {
        param($Providers, [string]$Prefix)
        if ($null -ne $Providers.PSObject.Properties[$Prefix]) { return $Prefix }
        foreach ($prop in $Providers.PSObject.Properties) {
            if ($null -ne $prop.Value.PSObject.Properties['omniroute_id'] -and $prop.Value.omniroute_id -eq $Prefix) {
                return $prop.Name
            }
        }
        return $null
    }

    # Mirrors tools/registry.py's _leg_is_unavailable(): a route's own
    # unavailable_legs entry, or the leg's provider carrying available: false
    # registry-wide (spec 3.1's two operator-facing "this is down" flags).
    function Test-AutoOSLegUnavailable {
        param($Providers, $Leg, $Route)
        $ul = if ($Route.PSObject.Properties['unavailable_legs']) { $Route.unavailable_legs } else { $null }
        if ($null -ne $ul -and $ul.PSObject.Properties[$Leg]) {
            $entry = $ul.$Leg
            if ($entry.PSObject.Properties['available'] -and $entry.available -eq $false) { return $true }
        }
        $prefix = $Leg.Split('/', 2)[0]
        $providerId = Resolve-AutoOSProviderId $Providers $prefix
        if ($null -eq $providerId) { return $false }
        $p = $Providers.$providerId
        return ($null -ne $p -and $p.PSObject.Properties['available'] -and $p.available -eq $false)
    }

    # Every leg any route lists, grouped by the provider id it resolves to -
    # used only to find a provider none of whose legs can ever be served.
    $legsByProvider = @{}
    if ($null -ne $routes) {
        foreach ($routeProp in $routes.PSObject.Properties) {
            $route = $routeProp.Value
            if (-not $route.PSObject.Properties['legs'] -or $null -eq $route.legs) { continue }
            foreach ($leg in $route.legs) {
                $prefix = $leg.Split('/', 2)[0]
                $providerId = Resolve-AutoOSProviderId $providers $prefix
                if ($null -ne $providerId) {
                    if (-not $legsByProvider.ContainsKey($providerId)) { $legsByProvider[$providerId] = New-Object System.Collections.ArrayList }
                    [void]$legsByProvider[$providerId].Add(@($leg, $route))
                }
            }
        }
    }

    $map = [ordered]@{}
    $data = @{}
    # omniroute_id -> its own OpenAI-compatible endpoint, for the providers the
    # CLI has no built-in for (see Set-AutoOSProviderNode below). apply.sh
    # reads the same field from the same registry.
    $bases = @{}
    $skipped = New-Object System.Collections.ArrayList
    foreach ($prop in $providers.PSObject.Properties) {
        $entry = $prop.Value
        if (-not $entry.PSObject.Properties['omniroute_id'] -or $null -eq $entry.omniroute_id) { continue }
        # The api-keys.yml entry the value is read from: the provider's own
        # name, unless key_name shares another provider's key (MUSEAPI step 4:
        # meta_api reads the meta key). Asking api-keys.yml for a meta_api
        # nobody has would skip the connection. Keys are lower-cased when read
        # above, so match that.
        $keyName = $prop.Name.ToLowerInvariant()
        if ($entry.PSObject.Properties['key_name'] -and $entry.key_name) {
            $keyName = ([string]$entry.key_name).ToLowerInvariant()
        }
        if ($map.Contains($keyName) -and $map[$keyName] -ne $entry.omniroute_id) {
            # Two connections from one api-keys.yml name: the ordered map keys
            # the name once, so the second would be dropped without a word.
            # apply.sh's pair list cannot lose it; this keeps the two scripts'
            # registries honest in the same way.
            throw "api-keys.yml name '$keyName' is claimed by both $($map[$keyName]) and $($entry.omniroute_id) - give one of them its own key entry"
        }
        $legs = $legsByProvider[$prop.Name]
        # A provider with no leg anywhere is not "every leg unavailable" (it
        # is simply unused elsewhere) - only a used provider whose every leg
        # is down is skipped here.
        if ($null -ne $legs -and $legs.Count -gt 0) {
            $allUnavailable = $true
            foreach ($pair in $legs) {
                if (-not (Test-AutoOSLegUnavailable $providers $pair[0] $pair[1])) { $allUnavailable = $false; break }
            }
            if ($allUnavailable) {
                [void]$skipped.Add($entry.omniroute_id)
                continue
            }
        }
        $map[$keyName] = $entry.omniroute_id
        if ($entry.PSObject.Properties['api_base'] -and $entry.api_base) {
            $bases[$entry.omniroute_id] = [string]$entry.api_base
        }
        if ($entry.PSObject.Properties['provider_data'] -and $null -ne $entry.provider_data) {
            # Keep the plain JSON string: the 5.1-vs-7.x escaping branch below
            # needs a string, and ConvertTo-Json -Compress is stable across both.
            $data[$entry.omniroute_id] = ($entry.provider_data | ConvertTo-Json -Compress)
        }
    }
    [pscustomobject]@{ Map = $map; Data = $data; Skipped = @($skipped); Bases = $bases }
}
$registry = Get-AutoOSProviderMap (Join-Path $Root 'catalog\ai-registry.json')
$ProviderMap = $registry.Map
$ProviderData = $registry.Data
$ProviderSkipped = $registry.Skipped
$ProviderBases = $registry.Bases

# Build the --provider-specific-data JSON for the running shell. PowerShell
# 5.1 strips inner double quotes when marshalling to a native exe (the same
# class of bug as the embedded-python quoting fix in run-tests.ps1), so 5.1
# needs backslash-escaped quotes while pwsh 7 passes clean JSON through.
# The $ShellMajor override exists for the suite: it pins the branch under
# test instead of asserting on the live shell.
function Get-AutoOSProviderDataJson {
    param([string]$ProviderId, [int]$ShellMajor = $PSVersionTable.PSVersion.Major)
    if (-not $ProviderData.ContainsKey($ProviderId)) { return $null }
    $plain = $ProviderData[$ProviderId]
    if ($ShellMajor -lt 6) { return $plain -replace '"', '\"' }
    return $plain
}

# --- Provider nodes for ids the CLI does not know ----------------------------
# A registry provider with an api_base whose omniroute_id is not one of
# OmniRoute's built-ins cannot be added with `omniroute providers add` - the CLI
# has nothing to add and says "Unknown provider: meta-api" (measured 2026-09-28
# against omniroute 3.8.51). Such a provider is the gateway's own
# OpenAI-compatible *provider node* with an API-key connection bound to it,
# which is exactly what the dashboard creates. apply makes that node over REST
# and hands the connection back to the CLI, so the vendor key still travels as
# an environment variable and never appears in a command line. The manage key
# only ever goes into a request header (never argv). Mirrors
# configuration/omniroute/apply.sh.
$RestKey = if ($env:OMNIROUTE_API_KEY) { $env:OMNIROUTE_API_KEY } else { '' }

# Both keys are secrets: a CLI error that quotes one, or a gateway body that
# echoes one, must not reach the log (and the log is the only record an operator
# has when a headless run fails).
function Get-AutoOSRedactedText {
    param([string]$Text, [string[]]$Secrets)
    $out = $Text
    foreach ($secret in $Secrets) {
        if ($secret) { $out = $out.Replace($secret, '[REDACTED]') }
    }
    $out
}

# Three lines, 200 columns, control characters stripped - the CLI's reason, not
# its stack trace, and never an escape sequence that corrupts the log line.
function Show-AutoOSCliError {
    param([string]$Text, [string[]]$Secrets)
    if (-not $Text) { return }
    $clean = (Get-AutoOSRedactedText $Text $Secrets) -replace '[\x00-\x08\x0B-\x1F]', ''
    foreach ($line in @($clean -split "`r?`n" | Where-Object { $_ } | Select-Object -First 3)) {
        Write-Host "      $($line.Substring(0, [Math]::Min(200, $line.Length)))"
    }
}

# The provider ids and aliases the installed CLI knows, read once per run and
# cached (`providers available` answers 352 of them). Aliases count too, because
# the gateway rejects a node whose prefix collides with one - omniroute
# src/shared/constants/reservedProviderPrefixes.ts. An unreadable catalog is
# treated as "everything is built-in", which is the behaviour this script had
# before: a wrong decision here would spam provider nodes.
$script:BuiltinIds = @()
$script:BuiltinTried = $false
$script:BuiltinKnown = $false
function Test-AutoOSBuiltinProvider {
    param([string]$ProviderId)
    if (-not $script:BuiltinTried) {
        $script:BuiltinTried = $true
        $text = ''
        try { $text = & omniroute providers available --json 2>&1 | Out-String } catch { $text = '' }
        # The CLI prints its .env warning banner before the JSON document, so
        # the document starts at the first line that opens a brace or bracket.
        $lines = @($text -split "`r?`n")
        $start = -1
        for ($i = 0; $i -lt $lines.Count; $i++) {
            if ($lines[$i] -match '^\s*[\{\[]') { $start = $i; break }
        }
        if ($start -lt 0) {
            Write-Host "  ! the CLI's provider catalog is unreadable - treating every id as built-in"
        } else {
            try {
                $doc = ($lines[$start..($lines.Count - 1)] -join "`n") | ConvertFrom-Json
                $rows = if ($doc -is [Array]) { @($doc) } elseif ($doc.PSObject.Properties['providers']) { @($doc.providers) } else { @() }
                foreach ($row in $rows) {
                    if ($null -eq $row) { continue }
                    if ($row.PSObject.Properties['id']) { $script:BuiltinIds += [string]$row.id }
                    if ($row.PSObject.Properties['alias'] -and $row.alias) { $script:BuiltinIds += [string]$row.alias }
                }
                $script:BuiltinKnown = $true
            } catch {
                Write-Host "  ! the CLI's provider catalog is unreadable - treating every id as built-in"
            }
        }
    }
    if (-not $script:BuiltinKnown) { return $true }
    return ($script:BuiltinIds -contains $ProviderId)
}

# A provider needs a node only when the registry gives it an endpoint of its own
# AND the CLI has no built-in for its id - a built-in keeps the exact call the
# script made before, argv included.
function Test-AutoOSProviderNeedsNode {
    param([string]$ProviderId)
    if (-not $ProviderBases.ContainsKey($ProviderId)) { return $false }
    return (-not (Test-AutoOSBuiltinProvider $ProviderId))
}

$script:RestError = ''
# The gateway's REST surface, with the manage key in a header only. Returns the
# parsed document, or $null with $script:RestError set for the caller's line.
function Invoke-AutoOSRest {
    param([string]$Method, [string]$Path, [string]$Body)
    $script:RestError = ''
    $headers = @{ Authorization = "Bearer $RestKey" }
    try {
        if ($Body) {
            return Invoke-RestMethod -Uri "$Gateway$Path" -Method $Method -Headers $headers `
                -ContentType 'application/json' -Body $Body -TimeoutSec 20
        }
        return Invoke-RestMethod -Uri "$Gateway$Path" -Method $Method -Headers $headers -TimeoutSec 20
    } catch {
        # A transport failure carries no Response at all, so the status is
        # read conditionally and the exception text stays the reason.
        $resp = $_.Exception.Response
        $code = if ($resp -and $resp.StatusCode) { [string][int]$resp.StatusCode } else { '' }
        $why = if ($_.ErrorDetails -and $_.ErrorDetails.Message) { $_.ErrorDetails.Message } else { $_.Exception.Message }
        $script:RestError = "HTTP $code for $Path - $why"
        return $null
    }
}

function Get-AutoOSProviderNodeId {
    param([string]$Prefix)
    $doc = Invoke-AutoOSRest -Method GET -Path '/api/provider-nodes' -Body $null
    if ($null -eq $doc) { return '' }
    $rows = if ($doc -is [Array]) { @($doc) } elseif ($doc.PSObject.Properties['nodes']) { @($doc.nodes) } else { @() }
    foreach ($row in $rows) {
        if ($null -ne $row -and $row.PSObject.Properties['prefix'] -and $row.prefix -eq $Prefix) {
            return [string]$row.id
        }
    }
    return ''
}

# A connection bound to a node is invisible to `omniroute providers list` (its
# provider column holds the node id, not a hex id), so its own existence is read
# from the gateway. The document carries each connection's stored key, so it is
# searched and never printed.
function Test-AutoOSProviderConnection {
    param([string]$NodeId, [string]$Name)
    $doc = Invoke-AutoOSRest -Method GET -Path '/api/providers?limit=5000' -Body $null
    if ($null -eq $doc) { return $false }
    $rows = if ($doc -is [Array]) { @($doc) } elseif ($doc.PSObject.Properties['connections']) { @($doc.connections) } else { @() }
    foreach ($row in $rows) {
        if ($null -eq $row) { continue }
        if ($row.PSObject.Properties['provider'] -and $row.provider -eq $NodeId) { return $true }
        if ($row.PSObject.Properties['name'] -and $row.name -eq $Name) { return $true }
    }
    return $false
}

# "created" or "existing", so the caller says which one it was.
$script:NodeNote = ''
function Set-AutoOSProviderNode {
    param([string]$ProviderId)
    $script:NodeNote = ''
    $nodeId = Get-AutoOSProviderNodeId $ProviderId
    if ($nodeId) { $script:NodeNote = 'existing'; return $nodeId }
    # createProviderNodeSchema, omniroute
    # src/shared/validation/schemas/provider.ts:307-385: name, prefix, baseUrl,
    # and - for type "openai-compatible" - an apiType, or the write is refused.
    # The CLI's own POST sends no body at all (bin/cli/api-commands/
    # provider-nodes.mjs:18-25), so this is the REST call, not `omniroute api`.
    $body = [pscustomobject]@{
        name    = $ProviderId
        prefix  = $ProviderId
        type    = 'openai-compatible'
        apiType = 'chat'
        baseUrl = $ProviderBases[$ProviderId]
    } | ConvertTo-Json -Compress
    if ($null -eq (Invoke-AutoOSRest -Method POST -Path '/api/provider-nodes' -Body $body)) { return '' }
    # The created node is read back rather than trusted from the POST response:
    # the response shape is not the documented contract, the prefix is.
    $nodeId = Get-AutoOSProviderNodeId $ProviderId
    if (-not $nodeId) {
        $script:RestError = 'the gateway accepted the node but does not list it'
        return ''
    }
    $script:NodeNote = 'created'
    return $nodeId
}

$RegisteredNow = @()
Write-Host 'Providers:'
# A provider whose every route leg is registry-unavailable (task A5a) is
# reported here and never touched below - no key lookup, no existing-id
# check, no register call.
foreach ($providerId in $ProviderSkipped) {
    Write-Host "  - ${providerId}: all legs unavailable (skipped)"
}
# Connections that already exist are left alone: re-adding would either fail
# or duplicate them, and neither proves the pipeline works.
$existingIds = @()
# A down gateway (dry run) has no list to read; the plan then comes from the
# key file alone instead of from whatever else answers the CLI.
if (Test-Gateway) { try {
    $listText = & omniroute providers list 2>&1 | Out-String
    if ($LASTEXITCODE -eq 0) {
        foreach ($line in ($listText -split "`r?`n")) {
            if ($line -match '^\s*[0-9a-f]{6,}\s+(\S+)') { $existingIds += $Matches[1] }
        }
    }
} catch { $existingIds = @() } }
foreach ($keyName in $ProviderMap.Keys) {
    $providerId = $ProviderMap[$keyName]
    if ($existingIds -contains $providerId) {
        Write-Host "  = $providerId already registered"
        continue
    }
    if (-not $Keys.ContainsKey($keyName)) {
        Write-Host "  - $providerId : no key in api-keys.yml, skipped"
        continue
    }
    if ($DryRun) {
        # Say how the connection would be made, not only that it would be: an id
        # with no built-in needs its provider node first. Reading the CLI's
        # catalog is a read; the dry run creates no node and adds no key.
        if ((Test-Gateway) -and (Test-AutoOSProviderNeedsNode $providerId)) {
            Write-Host "  - $providerId : would create its provider node (OpenAI-compatible" `
                "endpoint $($ProviderBases[$providerId])) and add the key to it"
        }
        Write-Host "  - $providerId : would register (key from $keyName)"
        # The Catalog step below plans from this list too: a dry run has to say
        # it would refresh what it would just have registered.
        $RegisteredNow += $providerId
        continue
    }
    $addId = $providerId
    $nameArgs = @()
    if (Test-AutoOSProviderNeedsNode $providerId) {
        if (-not $RestKey) {
            Write-Host "  ! $providerId is not a built-in provider - it needs a gateway provider node"
            Write-Host '      and no manage key is available (OMNIROUTE_API_KEY): register it in the dashboard'
            continue
        }
        $nodeId = Set-AutoOSProviderNode $providerId
        if (-not $nodeId) {
            Write-Host "  ! $providerId provider node could not be created - register it in the dashboard"
            $why = if ($script:RestError) { $script:RestError } else { 'the gateway refused the request' }
            Show-AutoOSCliError $why @($Keys[$keyName], $RestKey)
            continue
        }
        if ($script:NodeNote -eq 'created') {
            Write-Host "  + ${providerId}: provider node created (prefix $providerId -> $($ProviderBases[$providerId]))"
        }
        if (Test-AutoOSProviderConnection $nodeId $providerId) {
            Write-Host "  = $providerId already registered"
            continue
        }
        # The key binds to the node, and the connection keeps the registry's
        # name so the gateway UI and --drift both read "meta-api".
        $addId = $nodeId
        $nameArgs = @('--name', $providerId)
    }
    $varName = 'AUTOOS_KEY_' + $keyName.ToUpperInvariant()
    # Save a pre-existing variable of the same name: apply must not clobber
    # (or delete, below) something the user's shell already had.
    $hadVar = Test-Path "Env:$varName"
    $oldVar = if ($hadVar) { (Get-Item "Env:$varName").Value } else { $null }
    Set-Item -Path "Env:$varName" -Value $Keys[$keyName]
    # The node's own id first, then the registry's name for the connection; a
    # built-in keeps the exact call (and argv) it made before.
    $addArgs = @('providers', 'add', $addId) + $nameArgs + @('--credential-env', $varName)
    $dataJson = Get-AutoOSProviderDataJson $providerId
    if ($null -ne $dataJson) {
        $addArgs += @('--provider-specific-data', $dataJson)
    }
    $addArgs += '--yes'
    # The CLI's stderr IS the reason a registration failed - it used to go to
    # $null, which is why "register it in the dashboard" was all anybody ever
    # saw. Captured here, redacted, three lines.
    $addOut = & omniroute @addArgs 2>&1 | Out-String
    if ($LASTEXITCODE -eq 0) { Write-Host "  + $providerId registered"; $RegisteredNow += $providerId }
    else {
        Write-Host "  ! $providerId registration failed - register it in the dashboard"
        Show-AutoOSCliError $addOut @($Keys[$keyName], $RestKey)
    }
    if ($hadVar) { Set-Item -Path "Env:$varName" -Value $oldVar }
    else { Remove-Item -Path "Env:$varName" -ErrorAction SilentlyContinue }
}

# --- Refresh the gateway's model catalog -------------------------------------
# Registering a connection does not by itself put that provider's models in
# /v1/models: the gateway enumerates a provider when it is asked for its models.
# Reading the catalog before that gave a freshly registered provider no entries
# to validate against, so its leg was dropped as "catalog does not know" and
# only appeared on the SECOND apply run (L0 2026-09-27T19:07:39Z, free-ai/qwen7b
# on a fresh machine). Order is therefore register -> refresh -> read -> combos,
# the same order configuration/omniroute/apply.sh uses.
Write-Host 'Catalog:'
if ($DryRun) {
    foreach ($providerId in $RegisteredNow) {
        Write-Host "  - would refresh $providerId's models (omniroute models $providerId)"
    }
} elseif ($RegisteredNow.Count -eq 0) {
    Write-Host '  = nothing new registered - the catalog is already current'
} else {
    foreach ($providerId in $RegisteredNow) {
        & omniroute models $providerId *> $null
        if ($LASTEXITCODE -eq 0) { Write-Host "  + $providerId enumerated" }
        else { Write-Host "  ! $providerId could not be enumerated - its legs may be dropped below" }
    }
}

# --- Live catalog for validation ---
$LiveIds = @()
if ($Keys.ContainsKey('omniroute')) {
    try {
        $resp = Invoke-RestMethod -Uri "$Gateway/v1/models" -TimeoutSec 15 `
            -Headers @{ Authorization = "Bearer $($Keys['omniroute'])" }
        $LiveIds = @($resp.data | ForEach-Object { $_.id })
    } catch { $LiveIds = @() }
}
if ($LiveIds.Count -eq 0) {
    Write-Host '  ! could not read /v1/models (check the omniroute client key in api-keys.yml)'
    Write-Host '    combos will be created without catalog validation - verify with:'
    Write-Host '    omniroute simulate --combo t1-orchestrator'
}

# --- Resilience: fit a reasoning model AND fast-skip a dead leg ------------
# 1. requestQueue.maxWaitMs ships at 15000 ms, which is below Muse Spark's own
#    thinking time: every spark request died with "Request exceeded OmniRoute's
#    local rate-limit execution expiration", the chain fell through to a dead
#    free leg, and the client saw the last leg's error (measured 2026-09-22).
#    180000 ms sits under comboCooldownWait.budgetMs (300s) so a genuinely dead
#    leg still hops instead of stalling the run.
# 2. providerBreaker.apikey.failureThreshold 12 -> 2 (operator 2026-09-23):
#    the zen free promo stays FIRST (free when it works), but a 403 is a
#    permanent-class error, so at the shipped threshold the gateway retried the
#    dead promo on EVERY request. At 2 it is skipped for resetTimeoutMs (30s)
#    after two failures, then retried - at most two cheap round-trips, and
#    every other failing free leg hops fast too.
$MaxWaitMs = 180000
$Breaker = @{ failureThreshold = 2; degradationThreshold = 1; resetTimeoutMs = 30000 }
Write-Host 'Resilience:'
# Through the CLI, not Invoke-RestMethod + the client key: /api/resilience is
# a management route and answers the client key with 403 "Invalid management
# token" (measured 2026-09-24); the local CLI sends the machine loopback token.
$body = @{
    requestQueue    = @{ maxWaitMs = $MaxWaitMs }
    providerBreaker = @{ apikey = $Breaker }
} | ConvertTo-Json -Depth 5 -Compress
if ($DryRun) {
    Write-Host "  - would set requestQueue.maxWaitMs = $MaxWaitMs (omniroute api system patch-api-resilience)"
    Write-Host "  - would set providerBreaker.apikey.failureThreshold = $($Breaker.failureThreshold)"
} elseif (-not (Get-Command omniroute -ErrorAction SilentlyContinue)) {
    Write-Host '  - omniroute CLI missing - cannot set the resilience settings'
} else {
    $current = $null
    $currentBreaker = $null
    try {
        # The CLI prints "Loaded env" banners before the JSON document.
        $raw = (& omniroute --output json --no-color api system get-api-resilience 2>$null | Out-String)
        $start = $raw.IndexOf('{')
        if ($start -ge 0) {
            $cfg = $raw.Substring($start) | ConvertFrom-Json
            $current = $cfg.requestQueue.maxWaitMs
            $currentBreaker = $cfg.providerBreaker.apikey.failureThreshold
        }
    } catch { $current = $null; $currentBreaker = $null }
    if ($current -eq $MaxWaitMs -and $currentBreaker -eq $Breaker.failureThreshold) {
        Write-Host "  = resilience settings already current (maxWaitMs=$MaxWaitMs, breaker=$($Breaker.failureThreshold))"
    } else {
        # A body file, not an argument: PowerShell 5.1 mangles embedded quotes
        # when passing JSON to a native command.
        $bodyFile = [System.IO.Path]::GetTempFileName()
        try {
            [System.IO.File]::WriteAllText($bodyFile, $body)
            & omniroute api system patch-api-resilience --body "@$bodyFile" *> $null
            if ($LASTEXITCODE -eq 0) {
                Write-Host "  + maxWaitMs $current -> $MaxWaitMs; breaker $currentBreaker -> $($Breaker.failureThreshold)"
            } else {
                Write-Host '  ! could not set resilience settings - use the dashboard (Resilience)'
            }
        } finally { Remove-Item -LiteralPath $bodyFile -ErrorAction SilentlyContinue }
    }
}

# --- (Re)create combos --- ---
Write-Host 'Combos:'
$comboDoc = Get-Content $CombosFile -Raw -Encoding utf8 | ConvertFrom-Json
$combos = $comboDoc.combos
$created = @()
foreach ($combo in $combos) {
    $keep = @()
    $dropped = @()
    foreach ($ref in $combo.models) {
        if ($LiveIds.Count -eq 0 -or $LiveIds -contains $ref) { $keep += $ref }
        else { $dropped += $ref }
    }
    if ($dropped.Count -gt 0) {
        Write-Host "  - $($combo.name): catalog does not know $($dropped -join ', ') (skipped)"
    }
    if ($keep.Count -eq 0) {
        Write-Host "  ! $($combo.name): no usable models - not created"
        continue
    }
    if ($DryRun) {
        Write-Host "  - $($combo.name): would create [$($combo.strategy)] with $($keep -join ',')"
        continue
    }
    # Create first, delete only what it replaces: if the create fails the old
    # tier survives instead of leaving a hole.
    & omniroute combo create $combo.name --strategy $combo.strategy --models ($keep -join ',') *> $null
    if ($LASTEXITCODE -eq 0) {
        Write-Host "  + $($combo.name) created ($($combo.strategy))"
        $created += $combo.name
    } else {
        & omniroute combo delete $combo.name --yes *> $null
        & omniroute combo create $combo.name --strategy $combo.strategy --models ($keep -join ',') *> $null
        if ($LASTEXITCODE -eq 0) {
            Write-Host "  + $($combo.name) replaced ($($combo.strategy))"
            $created += $combo.name
        }
        else { Write-Host "  ! $($combo.name) creation failed (previous version, if any, is untouched)" }
    }
}

# --- Prune: delete the retired and omitted combos, and only those ---
# combos.json carries two lists of ids the live store should not hold:
# "retired" (a rename or removal left them behind; 9 orphans were deleted by
# hand on 2026-09-25) and "omitted" (OR1g: a route that declared legs but the
# registry renders no combo for, because it has no gateway-servable leg). The
# loop above only creates and replaces by name, so such an id would otherwise
# stay in the store forever. A name is deleted only when it is in one of those
# lists AND live (and not a current combo): a store combo in neither list may
# be one the user made and is never touched. A deliberately legless route
# (legs: [] - the LiteLLM-only *-paid and auto* routes) is in neither list by
# construction, so a live combo with one of its ids is never pruned.
# A down gateway is not listed at all: the CLI would fall back to reading the
# store file directly, and a dry run must not depend on that.
Write-Host 'Prune:'
$currentNames = @($combos | ForEach-Object { $_.name })
if (-not (Get-Command omniroute -ErrorAction SilentlyContinue)) {
    Write-Host '  - omniroute CLI missing - the store is not read, nothing pruned'
} elseif (-not (Test-Gateway)) {
    Write-Host '  - gateway down - the store is not read, nothing pruned'
} else {
    $liveNames = @()
    $listed = $false
    try {
        $listText = & omniroute combo list 2>$null | Out-String
        $listed = ($LASTEXITCODE -eq 0)
        # `combo list` prints "  <icon> <name padded> [<strategy>] <status>"
        # with ANSI colour on the icon and the status: strip it, take the name
        # before [.
        foreach ($line in ($listText -split "`r?`n")) {
            $plain = $line -replace "$([char]27)\[[0-9;]*m", ''
            if ($plain -match '^\s*\S+\s+(\S+)\s+\[[^\]]*\]') { $liveNames += $Matches[1] }
        }
    } catch { $listed = $false }
    if (-not $listed) {
        Write-Host "  ! could not list the store's combos - nothing pruned"
    } else {
        # Every managed orphan, labelled with the list it came from. The two
        # lists are disjoint by construction, but the store is external: a name
        # in both is printed once, "retired" first (the older fact) - the same
        # order apply.sh uses.
        $pruneNames = @()
        $seen = @()
        foreach ($kind in @('retired', 'omitted')) {
            if ($comboDoc.PSObject.Properties[$kind]) {
                foreach ($name in @($comboDoc.$kind)) {
                    if ($liveNames -ccontains $name -and $currentNames -cnotcontains $name -and $seen -cnotcontains $name) {
                        $seen += $name
                        $pruneNames += [pscustomobject]@{ Kind = $kind; Name = $name }
                    }
                }
            }
        }
        if ($pruneNames.Count -eq 0) {
            Write-Host '  = no retired or omitted combos in the store'
        }
        foreach ($prune in $pruneNames) {
            if ($DryRun) {
                Write-Host "  - $($prune.Name): $($prune.Kind), would delete"
                continue
            }
            & omniroute combo delete $prune.Name --yes *> $null
            if ($LASTEXITCODE -eq 0) { Write-Host "  - $($prune.Name): $($prune.Kind), deleted" }
            else { Write-Host "  ! $($prune.Name): $($prune.Kind), delete failed - run: omniroute combo delete $($prune.Name) --yes" }
        }
    }
}

# --- Probe: prove the combos answer, end to end ---
if ($Probe) {
    $probeKey = if ($Keys.ContainsKey('omniroute')) { $Keys['omniroute'] } else { '' }
    if (-not $probeKey -or $DryRun) {
        Write-Host 'Probe skipped (dry run, or no omniroute client key in api-keys.yml).'
    } elseif ($created.Count -eq 0) {
        Write-Host 'Probe skipped (no combos were created).'
    } else {
        Write-Host 'Probe (one tiny request per combo):'
        foreach ($name in $created) {
            $body = @{
                model = $name
                messages = @(@{ role = 'user'; content = 'Reply with: ok' })
                # 2048, not 256: reasoning models (spark) spend tokens on hidden
                # thinking first and answer "empty" when the budget is tiny.
                max_tokens = 2048
            } | ConvertTo-Json -Depth 5
            try {
                $r = Invoke-RestMethod -Uri "$Gateway/v1/chat/completions" -Method Post `
                    -Body $body -ContentType 'application/json' -TimeoutSec 300 `
                    -Headers @{ Authorization = "Bearer $probeKey" }
                Write-Host ("  + {0,-12} served by {1}" -f $name, $r.model)
            } catch {
                $detail = $_.Exception.Message
                if ($_.ErrorDetails -and $_.ErrorDetails.Message) {
                    $detail = $_.ErrorDetails.Message.Substring(0, [Math]::Min(200, $_.ErrorDetails.Message.Length))
                }
                Write-Host ("  ! {0,-12} {1}" -f $name, $detail)
            }
        }
    }
}

Write-Host 'Done. Apps pick this up on next start (configuration\start-stack.ps1).'
