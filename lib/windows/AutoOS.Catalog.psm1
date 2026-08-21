#Requires -Version 5.1
<#
.SYNOPSIS
    Load the component catalog, filter it against the detected machine, and
    resolve dependencies into an ordered install plan.

.DESCRIPTION
    No side effects. Given a catalog and a system-info object this module always
    returns the same plan, which is what lets --dry-run mean something and lets
    the tests run without touching the machine.
#>

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$script:ValidProviders = @('winget', 'choco', 'npm', 'apt', 'snap', 'brew', 'script', 'custom')

function Get-AutoOSCatalog {
    param([Parameter(Mandatory)][string]$Path)
    if (-not (Test-Path $Path)) { throw "Catalog not found: $Path" }
    $raw = Get-Content -Path $Path -Raw -Encoding UTF8
    try { $raw | ConvertFrom-Json } catch { throw "Catalog is not valid JSON ($Path): $($_.Exception.Message)" }
}

function Test-AutoOSCatalogSchema {
    <#
      .SYNOPSIS
        Validate every entry. Returns an array of problem strings; empty means valid.
      .DESCRIPTION
        Deliberately exhaustive rather than fail-fast, so one run reports every
        malformed entry instead of making the author fix them one at a time.
    #>
    param([Parameter(Mandatory)][psobject]$Catalog)

    $problems = New-Object System.Collections.ArrayList
    $seen = @{}

    if (-not $Catalog.PSObject.Properties.Name.Contains('categories')) {
        [void]$problems.Add('catalog: missing "categories"'); return $problems
    }

    $allIds = @()
    foreach ($cat in $Catalog.categories) { foreach ($c in $cat.components) { $allIds += $c.id } }

    foreach ($cat in $Catalog.categories) {
        if (-not $cat.id)   { [void]$problems.Add('category: missing "id"') }
        if (-not $cat.name) { [void]$problems.Add("category '$($cat.id)': missing ""name""") }

        foreach ($c in $cat.components) {
            $where = "component '$($c.id)'"
            if (-not $c.id)          { [void]$problems.Add("$($cat.id): a component has no ""id""") ; continue }
            if ($seen.ContainsKey($c.id)) { [void]$problems.Add("$where : duplicate id") }
            $seen[$c.id] = $true

            if ($c.id -cnotmatch '^[a-z0-9][a-z0-9-]*$') { [void]$problems.Add("$where : id must be lower-case kebab-case") }
            if (-not $c.name)        { [void]$problems.Add("$where : missing ""name""") }
            if (-not $c.description) { [void]$problems.Add("$where : missing ""description""") }
            elseif ($c.description.Length -gt 70) { [void]$problems.Add("$where : description longer than 70 chars") }
            if (-not $c.provider)    { [void]$problems.Add("$where : missing ""provider""") }
            elseif ($c.provider -notin $script:ValidProviders) {
                [void]$problems.Add("$where : unknown provider '$($c.provider)'")
            }
            if (-not $c.package)     { [void]$problems.Add("$where : missing ""package""") }

            if ($c.PSObject.Properties.Name -contains 'requires') {
                foreach ($r in $c.requires) {
                    if ($r -notin $allIds) { [void]$problems.Add("$where : requires unknown component '$r'") }
                    if ($r -eq $c.id)      { [void]$problems.Add("$where : requires itself") }
                }
            }
            if ($c.PSObject.Properties.Name -contains 'verify' -and [string]::IsNullOrWhiteSpace($c.verify)) {
                [void]$problems.Add("$where : 'verify' is present but empty")
            }
            if ($c.PSObject.Properties.Name -contains 'homepage') {
                if ($c.homepage -notmatch '^https?://') {
                    [void]$problems.Add("$where : 'homepage' must be an http(s) URL")
                }
            }
            if ($c.PSObject.Properties.Name -contains 'prompt') {
                if (-not $Catalog.prompts -or -not $Catalog.prompts.PSObject.Properties.Name.Contains($c.prompt)) {
                    [void]$problems.Add("$where : references undefined prompt '$($c.prompt)'")
                }
            }
        }
    }
    $problems
}

function Get-AutoOSComponentProperty {
    param([psobject]$Component, [string]$Name, $Default = $null)
    if ($Component.PSObject.Properties.Name -contains $Name) { $Component.$Name } else { $Default }
}

function Get-AutoOSAvailableComponents {
    <#
      .SYNOPSIS
        Flatten the catalog to components that can actually run here.
      .DESCRIPTION
        A component that cannot run on this machine is HIDDEN rather than shown
        and later failed - offering a choice that cannot work is worse than not
        offering it.
    #>
    param(
        [Parameter(Mandatory)][psobject]$Catalog,
        [Parameter(Mandatory)][psobject]$SystemInfo
    )

    $out = New-Object System.Collections.ArrayList
    foreach ($cat in $Catalog.categories) {
        $needsDisplay = [bool](Get-AutoOSComponentProperty $cat 'requiresDisplay' $false)
        if ($needsDisplay -and $SystemInfo.IsHeadless) { continue }

        foreach ($c in $cat.components) {
            $arch = Get-AutoOSComponentProperty $c 'arch' $null
            if ($arch -and ($SystemInfo.Arch -notin $arch)) { continue }

            [void]$out.Add([pscustomobject]@{
                Id          = $c.id
                Name        = $c.name
                Description = $c.description
                Provider    = $c.provider
                Package     = $c.package
                Source      = Get-AutoOSComponentProperty $c 'source' $null
                Cask        = [bool](Get-AutoOSComponentProperty $c 'cask' $false)
                Requires    = @(Get-AutoOSComponentProperty $c 'requires' @())
                Profiles    = @(Get-AutoOSComponentProperty $c 'profiles' @())
                PostInstall = Get-AutoOSComponentProperty $c 'postInstall' $null
                Prompt      = Get-AutoOSComponentProperty $c 'prompt' $null
                Verify      = Get-AutoOSComponentProperty $c 'verify' $null
                Homepage    = Get-AutoOSComponentProperty $c 'homepage' $null
                Notes       = Get-AutoOSComponentProperty $c 'notes' $null
                Category    = $cat.name
                CategoryId  = $cat.id
            })
        }
    }
    $out
}

function New-AutoOSMenuItem {
    <#
      .SYNOPSIS Shape an available component into a row the menu understands.
    #>
    param(
        [Parameter(Mandatory)][psobject]$Component,
        # Not $Profile: that is a PowerShell automatic variable ($PROFILE).
        [Alias('Profile')][string]$ProfileName = 'custom'
    )
    [pscustomobject]@{
        Id          = $Component.Id
        Name        = $Component.Name
        Description = $Component.Description
        Group       = $Component.Category
        Selected    = ($ProfileName -ne 'custom' -and $ProfileName -in $Component.Profiles)
        Locked      = $false
        Reason      = ''
    }
}

function Resolve-AutoOSPlan {
    <#
      .SYNOPSIS
        Turn a set of chosen ids into an ordered, dependency-complete plan.
      .DESCRIPTION
        Pulls in transitive requirements automatically and topologically sorts
        the result, so the catalog never has to be hand-ordered. Throws on a
        dependency cycle rather than silently dropping an entry.
      .OUTPUTS
        Ordered component objects, dependencies first. Each carries AutoAdded.
    #>
    param(
        [Parameter(Mandatory)][AllowEmptyCollection()][object[]]$Available,
        [Parameter(Mandatory)][AllowEmptyCollection()][string[]]$SelectedIds
    )

    $byId = @{}
    foreach ($c in $Available) { $byId[$c.Id] = $c }

    # transitive closure of requirements
    $wanted = New-Object System.Collections.Generic.HashSet[string]
    $queue  = New-Object System.Collections.Queue
    foreach ($id in $SelectedIds) { if ($byId.ContainsKey($id)) { [void]$queue.Enqueue($id) } }
    while ($queue.Count -gt 0) {
        $id = $queue.Dequeue()
        if (-not $wanted.Add($id)) { continue }
        foreach ($dep in $byId[$id].Requires) {
            if ($byId.ContainsKey($dep)) { [void]$queue.Enqueue($dep) }
        }
    }

    # depth-first topological sort
    $ordered   = New-Object System.Collections.ArrayList
    $permanent = New-Object System.Collections.Generic.HashSet[string]
    $temporary = New-Object System.Collections.Generic.HashSet[string]

    function Add-AutoOSTopoNode {
        param([string]$Id, [System.Collections.Generic.List[string]]$Path)
        if ($permanent.Contains($Id)) { return }
        if ($temporary.Contains($Id)) {
            throw "Dependency cycle in catalog: $((@($Path) + $Id) -join ' -> ')"
        }
        [void]$temporary.Add($Id)
        $null = $Path.Add($Id)
        foreach ($dep in $byId[$Id].Requires) {
            if ($wanted.Contains($dep)) { Add-AutoOSTopoNode -Id $dep -Path $Path }
        }
        $Path.RemoveAt($Path.Count - 1)
        [void]$temporary.Remove($Id)
        [void]$permanent.Add($Id)

        $node = $byId[$Id].PSObject.Copy()
        Add-Member -InputObject $node -NotePropertyName AutoAdded `
                   -NotePropertyValue (-not ($SelectedIds -contains $Id)) -Force
        [void]$ordered.Add($node)
    }

    foreach ($id in $wanted) {
        Add-AutoOSTopoNode -Id $id -Path (New-Object System.Collections.Generic.List[string])
    }
    $ordered
}

Export-ModuleMember -Function `
    Get-AutoOSCatalog, Test-AutoOSCatalogSchema, Get-AutoOSAvailableComponents,
    New-AutoOSMenuItem, Resolve-AutoOSPlan, Get-AutoOSComponentProperty
