<#
.SYNOPSIS
  Apply the AutoOS router configuration to OmniRoute (Windows).

.DESCRIPTION
  1. Registers every provider key found in configuration/api-keys.yml.
  2. Refreshes the gateway's model catalog for what it just registered
     (omniroute models <provider>), so one run is enough for a fresh machine.
  3. (Re)creates the tier combos from configuration/omniroute/combos.json.
  4. Prunes the combos listed there as "retired" or "omitted" from the store
     (only those).

  B2-VERTEX 2026-10-01: vertex authenticates from the GCP service-account JSON
  file configuration/vertex-credentials-autoos-510210-9fdf2297df6f.json (full
  content is the credential, NOT a plain API key; git-ignored), not from
  api-keys.yml. Credential-store repair (two vertex/meta connections, one
  undecryptable 401) is L0's, not apply's.

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
        if ($entry.PSObject.Properties['provider_data'] -and $null -ne $entry.provider_data) {
            # Keep the plain JSON string: the 5.1-vs-7.x escaping branch below
            # needs a string, and ConvertTo-Json -Compress is stable across both.
            $data[$entry.omniroute_id] = ($entry.provider_data | ConvertTo-Json -Compress)
        }
    }
    [pscustomobject]@{ Map = $map; Data = $data; Skipped = @($skipped) }
}
$registry = Get-AutoOSProviderMap (Join-Path $Root 'catalog\ai-registry.json')

# --- Management REST (local gateway) -----------------------------------------
# The management API accepts the machine loopback token the local CLI sends
# (omniroute docs/security/CLI_TOKEN.md): HMAC-SHA256 of the machine id, keyed
# by that id, over the salt "omniroute-cli-auth-v1", sent as the
# x-omniroute-cli-token header. apply needs it for the two routes the CLI cannot
# call with a body in omniroute 3.8.50: POST /api/provider-nodes (`omniroute
# nodes add` cannot parse its own --base-url) and PATCH
# /api/model-capability-overrides. Computed once; on any failure the dependent
# steps say so and fall back to the dashboard instead of guessing.
function Get-AutoOSManageToken {
    try {
        $guid = (Get-ItemProperty 'HKLM:\SOFTWARE\Microsoft\Cryptography' -ErrorAction Stop).MachineGuid
        if (-not $guid) { return '' }
        $hmac = New-Object System.Security.Cryptography.HMACSHA256
        $hmac.Key = [Text.Encoding]::UTF8.GetBytes([string]$guid)
        $digest = $hmac.ComputeHash([Text.Encoding]::UTF8.GetBytes('omniroute-cli-auth-v1'))
        return (-join ($digest | ForEach-Object { $_.ToString('x2') }))
    } catch { return '' }
}
$ManageToken = Get-AutoOSManageToken

function Invoke-AutoOSGateway {
    param([string]$Method, [string]$Path, $Body = $null)
    $script:GatewayError = ''
    if ([string]::IsNullOrEmpty($ManageToken)) {
        $script:GatewayError = 'no machine token available'
        return $null
    }
    try {
        $call = @{ Uri = "$Gateway$Path"; Method = $Method; TimeoutSec = 15
                   Headers = @{ 'x-omniroute-cli-token' = $ManageToken } }
        if ($null -ne $Body) {
            $call.Body = ($Body | ConvertTo-Json -Depth 6 -Compress)
            $call.ContentType = 'application/json'
        }
        return Invoke-RestMethod @call
    } catch {
        $script:GatewayError = $_.Exception.Message
        return $null
    }
}

# api_base per provider: the endpoint a provider node would carry. Read straight
# from the registry - the map above only carries what registration needs.
$ProviderBase = @{}
try {
    $regDoc = Get-Content (Join-Path $Root 'catalog\ai-registry.json') -Raw -Encoding utf8 | ConvertFrom-Json
    foreach ($p in $regDoc.providers.PSObject.Properties) {
        $e = $p.Value
        if ($e.PSObject.Properties['omniroute_id'] -and $e.omniroute_id -and
            $e.PSObject.Properties['api_base'] -and $e.api_base) {
            $ProviderBase[[string]$e.omniroute_id] = [string]$e.api_base
        }
    }
} catch { $ProviderBase = @{} }

# The CLI's built-in provider ids and aliases, read once. A registry provider
# whose omniroute_id is not among them (meta_api -> meta-api) needs a gateway
# provider NODE before its key can bind - the same rule apply.sh carries since
# MUSEFIX. An unreadable catalog means "everything is built-in": guessing the
# other way would create nodes on a machine where the CLI simply did not answer.
$BuiltinIds = $null
function Test-AutoOSBuiltinProvider {
    param([string]$Id)
    if ($null -eq $BuiltinIds) {
        $script:BuiltinIds = @()
        if (Get-Command omniroute -ErrorAction SilentlyContinue) {
            try {
                $raw = (& omniroute providers available --json 2>$null | Out-String)
                $start = $raw.IndexOf('{')
                if ($start -ge 0) {
                    $doc = $raw.Substring($start) | ConvertFrom-Json
                    $ids = New-Object System.Collections.ArrayList
                    foreach ($p in @($doc.providers)) {
                        if ($p.id) { [void]$ids.Add([string]$p.id) }
                        if ($p.alias) { [void]$ids.Add([string]$p.alias) }
                    }
                    $script:BuiltinIds = @($ids | Select-Object -Unique)
                }
            } catch { $script:BuiltinIds = @() }
        }
    }
    if ($BuiltinIds.Count -eq 0) { return $true }
    return ($BuiltinIds -contains $Id)
}
$ProviderMap = $registry.Map
$ProviderData = $registry.Data
$ProviderSkipped = $registry.Skipped

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
    # A registry provider whose spelling the CLI has no built-in connection for
    # (meta_api -> meta-api) registers through a gateway PROVIDER NODE: the node
    # owns the endpoint and the prefix, the key binds to the node. apply.sh has
    # carried this rule since MUSEFIX; the node is created over the management
    # REST route because the CLI cannot call it with a body (`omniroute nodes
    # add` cannot parse its own --base-url in 3.8.50).
    $apiBase = if ($ProviderBase.ContainsKey($providerId)) { $ProviderBase[$providerId] } else { '' }
    $needsNode = ($apiBase -ne '' -and -not (Test-AutoOSBuiltinProvider $providerId))
    if ($DryRun) {
        if ($needsNode) {
            Write-Host "  - $providerId : would ensure its provider node (prefix $providerId -> $apiBase) and bind the key to it"
        }
        Write-Host "  - $providerId : would register (key from $keyName)"
        # The Catalog step below plans from this list too: a dry run has to say
        # it would refresh what it would just have registered.
        $RegisteredNow += $providerId
        continue
    }
    $addId = $providerId
    $nameArgs = @()
    if ($needsNode) {
        if (-not $ManageToken) {
            Write-Host "  ! $providerId is not a built-in provider - it needs a gateway provider node, and no machine token is available; register it in the dashboard"
            continue
        }
        $nodesDoc = Invoke-AutoOSGateway -Method GET -Path '/api/provider-nodes'
        $node = @($nodesDoc.nodes) | Where-Object { $_.prefix -eq $providerId } | Select-Object -First 1
        if ($null -eq $node) {
            $null = Invoke-AutoOSGateway -Method POST -Path '/api/provider-nodes' -Body @{
                name = $providerId; prefix = $providerId; type = 'openai-compatible'
                apiType = 'chat'; baseUrl = $apiBase
            }
            if ($script:GatewayError) {
                Write-Host "  ! $providerId provider node could not be created ($($script:GatewayError)) - register it in the dashboard"
                continue
            }
            # Read the node back rather than trusting the POST response: the
            # prefix is the contract, the response shape is not.
            $nodesDoc = Invoke-AutoOSGateway -Method GET -Path '/api/provider-nodes'
            $node = @($nodesDoc.nodes) | Where-Object { $_.prefix -eq $providerId } | Select-Object -First 1
            if ($null -eq $node) {
                Write-Host "  ! ${providerId}: the gateway accepted the node but does not list it"
                continue
            }
            Write-Host "  + ${providerId}: provider node created (prefix $providerId -> $apiBase)"
        }
        # A node-bound connection's provider field is the node's "<type>-<uuid>"
        # id, which the hex-id scan above cannot see - ask the gateway itself so
        # a re-run cannot add a duplicate.
        $connsDoc = Invoke-AutoOSGateway -Method GET -Path '/api/providers?limit=5000'
        $bound = @($connsDoc.connections) | Where-Object {
            $_.provider -eq $node.id -or ($_.name -eq $providerId -and $_.provider -eq $providerId)
        } | Select-Object -First 1
        if ($null -ne $bound) {
            if ($bound.isActive -eq $false) { Write-Host "  ! ${providerId}: its connection is disabled in the dashboard" }
            else { Write-Host "  = $providerId already registered" }
            continue
        }
        $addId = $node.id
        $nameArgs = @('--name', $providerId)
    }
    $varName = 'AUTOOS_KEY_' + $keyName.ToUpperInvariant()
    # Save a pre-existing variable of the same name: apply must not clobber
    # (or delete, below) something the user's shell already had.
    $hadVar = Test-Path "Env:$varName"
    $oldVar = if ($hadVar) { (Get-Item "Env:$varName").Value } else { $null }
    Set-Item -Path "Env:$varName" -Value $Keys[$keyName]
    $addArgs = @('providers', 'add', $addId, '--credential-env', $varName)
    $dataJson = Get-AutoOSProviderDataJson $providerId
    if ($null -ne $dataJson) {
        $addArgs += @('--provider-specific-data', $dataJson)
    }
    $addArgs += $nameArgs
    $addArgs += '--yes'
    & omniroute @addArgs *> $null
    if ($LASTEXITCODE -eq 0) { Write-Host "  + $providerId registered"; $RegisteredNow += $providerId }
    else { Write-Host "  ! $providerId registration failed - register it in the dashboard" }
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
# 3. Fast failover (CTXFIX 2026-09-30): the breaker IS the fast-skip mechanism,
#    not maxWaitMs. degradationThreshold=1 hops on the first degradation signal
#    (a 429 counts), failureThreshold=2 opens the breaker after two hard
#    failures - at most two cheap round-trips before the leg is skipped for
#    resetTimeoutMs. maxWaitMs=180000 is the queue wait (Spark thinking time),
#    not the per-leg wait; a rate-limited leg returns 429 in < 1 s and the
#    chain hops immediately. No value change needed for this lane.
#    B2-LATENCY 2026-10-01 (t1 22556 ms crawl: gemini 503 -> free-ai 429 ->
#    vertex error -> meta-api 401 before deepseek served): live
#    get-api-resilience shows requestQueue.maxWaitMs 180000 (queue wait, NOT
#    per-leg), providerBreaker.apikey 2/1/30000, oauth 8/5/60000,
#    connectionCooldown apikey base 3000/maxBackoff 5 (oauth 5000/8),
#    waitForCooldown 3 retries/30 s, comboCooldownWait 90 s/5 attempts/300 s
#    budget, providerCooldown DISABLED. No per-leg timeout knob exists here
#    (no legTimeoutMs); the 30-min park + 3x429/120 s->300 s + CHAT_MAX_HEAVY=8
#    are gateway env (08:07Z restart), not patchable via patch-api-resilience.
#    Current == proposed (180000 / 2 / 1 / 30000). The crawl stops by REMOVING
#    dead legs (B2-HF/B2-AGY), shortening the chain, not by retuning.
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

# TORDER 2026-10-01: combo-contract gate (fail-closed, also under -DryRun).
# Every combo must satisfy tools/combo-contract.py (contexts, trial->free->
# credits->paid with paid last/deepseek last/no-free-after-paid, openrouter
# :free-only, resolve+live, t1 >=600k 1M, t2/t3 128k) before anything is created.
$contractScript = Join-Path $Root 'tools\combo-contract.py'
& python $contractScript
if ($LASTEXITCODE -ne 0) { Write-Host 'combo-contract failed - refusing to create combos'; exit 1 }

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

# --- Model context overrides ------------------------------------------------
# The gateway resolves a model's context window from the registry, the
# models.dev sync and the provider's own discovery; where none of them knows the
# model it falls back to 128000 - and a client whose own limit is higher gets
# rejected and compacts (the 2026-09-30 deepseek-v4.1-flash and
# spark-1.3-contributor failures: live sessions compacted every ~5 minutes).
# context-overrides.json lists the known-wrong resolutions; each is applied
# through the documented management route PATCH /api/model-capability-overrides
# {target, key: "context_length", value}. Idempotent: an override already at the
# wanted value is reported, not rewritten.
Write-Host 'Overrides:'
$overridesFile = Join-Path $Here 'context-overrides.json'
if (-not (Test-Path $overridesFile)) {
    Write-Host '  = no context-overrides.json - nothing to do'
} else {
    $overrideEntries = @()
    try {
        $overrideDoc = Get-Content $overridesFile -Raw -Encoding utf8 | ConvertFrom-Json
        $overrideEntries = @($overrideDoc.overrides)
    } catch {
        Write-Host "  ! $overridesFile is unreadable - nothing applied"
    }
    if ($overrideEntries.Count -eq 0 -and (Test-Path $overridesFile)) {
        Write-Host '  = context-overrides.json lists none'
    }
    foreach ($o in $overrideEntries) {
        if (-not $o.target -or -not $o.context) { continue }
        if ($DryRun) {
            Write-Host "  - would ensure $($o.target) context = $($o.context)"
            continue
        }
        if (-not $ManageToken) {
            Write-Host "  ! $($o.target): no machine token for the management API - set it in the dashboard (Model Overrides)"
            continue
        }
        $current = $null
        $overridesDoc = Invoke-AutoOSGateway -Method GET -Path '/api/model-capability-overrides'
        if ($null -eq $overridesDoc -and $script:GatewayError) {
            Write-Host "  ! $($o.target): the gateway did not answer ($($script:GatewayError))"
            continue
        }
        foreach ($row in @($overridesDoc.overrides)) {
            if ($row.target -eq $o.target -and $row.key -eq 'context_length') { $current = $row.value }
        }
        if ($current -eq $o.context) {
            Write-Host "  = $($o.target) already $($o.context)"
            continue
        }
        $null = Invoke-AutoOSGateway -Method PATCH -Path '/api/model-capability-overrides' -Body @{
            target = $o.target; key = 'context_length'; value = [int64]$o.context
        }
        if ($script:GatewayError) {
            Write-Host "  ! $($o.target) override failed ($($script:GatewayError)) - set it in the dashboard"
        } else {
            Write-Host "  + $($o.target) context -> $($o.context)"
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
