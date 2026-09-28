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

$script:ValidProviders = @('winget', 'choco', 'npm', 'apt', 'snap', 'brew', 'script', 'custom', 'manual', 'psmodule')

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
    $retiredIds = @()
    foreach ($cat in $Catalog.categories) {
        foreach ($c in $cat.components) {
            $allIds += $c.id
            if (Test-AutoOSTombstone -Component $c) { $retiredIds += $c.id }
        }
    }

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

            if ($c.PSObject.Properties.Name -contains 'profiles') {
                foreach ($profileName in $c.profiles) {
                    if ($profileName -notin $Catalog.profiles.PSObject.Properties.Name) { [void]$problems.Add("$where : unknown profile '$profileName'") }
                }
            }
            if ($c.provider -eq 'manual' -and (-not (Get-AutoOSComponentProperty $c 'homepage') -or -not (Get-AutoOSComponentProperty $c 'notes'))) { [void]$problems.Add("$where : manual provider requires homepage and notes") }
            if ($c.PSObject.Properties.Name -contains 'requires') {
                foreach ($r in $c.requires) {
                    if ($r -notin $allIds) { [void]$problems.Add("$where : requires unknown component '$r'") }
                    if ($r -eq $c.id)      { [void]$problems.Add("$where : requires itself") }
                    # A dependency that installs nothing can never be satisfied,
                    # so depending on one would leave the dependent skipped for a
                    # reason nobody can read off the catalog.
                    if ($r -in $retiredIds) { [void]$problems.Add("$where : requires '$r', a tombstone that installs nothing") }
                }
            }
            if ($c.PSObject.Properties.Name -contains 'verify' -and [string]::IsNullOrWhiteSpace($c.verify)) {
                [void]$problems.Add("$where : 'verify' is present but empty")
            }
            # A tombstone is a deliberate shape, not a shortcut: the id stays
            # published so saved selections resolve, and everything that only
            # makes sense for something that installs (postInstall, prompt,
            # requires, verify) is allowed to be absent - which is why these
            # checks test the field rather than demanding the usual set.
            if ($c.PSObject.Properties.Name -contains 'tombstone') {
                $flag = Get-AutoOSComponentProperty $c 'tombstone' $null
                if ($flag -isnot [bool] -or -not $flag) {
                    [void]$problems.Add("$where : 'tombstone' must be the boolean true")
                }
            }
            if ($c.PSObject.Properties.Name -contains 'note') {
                if ([string]::IsNullOrWhiteSpace($c.note)) {
                    [void]$problems.Add("$where : 'note' is present but empty")
                }
                if (-not (Test-AutoOSTombstone -Component $c)) {
                    [void]$problems.Add("$where : 'note' is only meaningful on a tombstone entry")
                }
            }
            # 'replaced_by' is what keeps a retirement's work on a replay: the ids
            # the catalog says took it over. They have to exist and install
            # something, because expanding an old state file plans exactly these
            # ids - a name that resolves to another tombstone would replay into a
            # row that can only report skipped, which is the defect this field
            # exists to close.
            if ($c.PSObject.Properties.Name -contains 'replaced_by') {
                $rb = Get-AutoOSComponentProperty $c 'replaced_by' $null
                if (-not (Test-AutoOSTombstone -Component $c)) {
                    [void]$problems.Add("$where : 'replaced_by' is only meaningful on a tombstone entry")
                }
                if ($rb -isnot [System.Array] -or @($rb | Where-Object { $_ }).Count -eq 0) {
                    [void]$problems.Add("$where : 'replaced_by' must be a non-empty list of component ids")
                }
                foreach ($r in @($rb | Where-Object { $_ })) {
                    if ($r -notin $allIds)     { [void]$problems.Add("$where : 'replaced_by' names unknown component '$r'") }
                    if ($r -in $retiredIds)    { [void]$problems.Add("$where : 'replaced_by' names '$r', a tombstone that installs nothing") }
                }
            }
            if ($c.PSObject.Properties.Name -contains 'homepage') {
                if ($c.homepage -notmatch '^https?://') {
                    [void]$problems.Add("$where : 'homepage' must be an http(s) URL")
                }
            }
            if ($c.PSObject.Properties.Name -contains 'prompt') {
                $keys = $c.prompt -split '[, ]+' | Where-Object { $_ }
                foreach ($k in $keys) {
                    if (-not $Catalog.prompts -or -not $Catalog.prompts.PSObject.Properties.Name.Contains($k)) {
                        [void]$problems.Add("$where : references undefined prompt '$k'")
                    }
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

function Test-AutoOSTombstone {
    <#
      .SYNOPSIS
        Is this entry retired? The one place the 'tombstone' field is read.
      .DESCRIPTION
        Reads either shape a caller holds: the raw JSON object of the catalog
        file (lower-case 'tombstone') and the flattened projection from
        Get-AutoOSAvailableComponents (upper-case 'Tombstone') - the property
        names are matched case-insensitively, so -Installed, the menu and the
        plan all ask the same question.
        Missing field and $null are both "not retired"; a truthy string is not
        the boolean true, because PowerShell would coerce '1' and a catalog typo
        would silently retire a component that still installs.
    #>
    param([Parameter(Mandatory)][psobject]$Component)
    $flag = Get-AutoOSComponentProperty $Component 'tombstone' $null
    ($flag -is [bool]) -and $flag
}

function Get-AutoOSTombstoneNote {
    <# .SYNOPSIS The retired entry's own note, or an empty string. #>
    param([Parameter(Mandatory)][psobject]$Component)
    # 'RetireNote' on the flattened projection, 'note' on the raw JSON entry.
    $note = Get-AutoOSComponentProperty $Component 'RetireNote' $null
    if (-not $note) { $note = Get-AutoOSComponentProperty $Component 'note' '' }
    if ($note) { [string]$note } else { '' }
}

function Format-AutoOSTombstoneSkip {
    <#
      .SYNOPSIS
        The result line a retired component reports.
      .DESCRIPTION
        'skipped' is the only outcome a tombstone can have: it is never
        installed and never failed, so a saved selection replays clean and a
        real run reports nothing went wrong. Wording lives here so setup.ps1
        and the suite read the same string rather than each spelling it out.
    #>
    param([Parameter(Mandatory)][psobject]$Component)
    $note = Get-AutoOSTombstoneNote -Component $Component
    if ($note) { "skipped: retired ($note)" } else { 'skipped: retired' }
}

function Get-AutoOSTombstoneReplacements {
    <#
      .SYNOPSIS
        The ids a retired entry names as taking its work on, as a plain array.
      .DESCRIPTION
        Reads 'ReplacedBy' on the flattened projection and 'replaced_by' on the
        raw JSON entry, the same pair Get-AutoOSTombstoneNote reads, so the
        expansion asks one question rather than two.
    #>
    param([Parameter(Mandatory)][psobject]$Component)
    $rb = Get-AutoOSComponentProperty $Component 'ReplacedBy' $null
    if ($null -eq $rb) { $rb = Get-AutoOSComponentProperty $Component 'replaced_by' @() }
    @($rb | Where-Object { $_ })
}

function Format-AutoOSTombstoneReplacement {
    <#
      .SYNOPSIS
        The muted line a replay prints when a retired id stands for other work.
      .DESCRIPTION
        One home for the wording, as with the skip line: setup.ps1 and the suite
        read the same string rather than each spelling it out.
    #>
    param(
        [Parameter(Mandatory)][psobject]$Component,
        [Parameter(Mandatory)][string[]]$Replacements
    )
    "$($Component.Id) is retired: replaced by $($Replacements -join ', ')"
}

function Expand-AutoOSTombstoneReplacements {
    <#
      .SYNOPSIS
        A selection with every retired id followed by the ids that replaced it.
      .DESCRIPTION
        A state file saved before a component was retired names the retired id and
        none of its successors, so replaying it booked the retirement and installed
        nothing - the work the user had simply stopped happening. Every selection
        built from explicit ids is expanded here: -FromState, -Only, and a browser
        run, whose -Serve passes the selection through as -Only. A profile never
        names a tombstone, so a profile run never comes through.

        Returns @{ Ids; Lines }. The retired row stays in Ids - it is what still
        reports 'skipped: retired' - and Lines is one announcement per retired id
        that has successors in the plan. A successor this machine does not offer is
        left out, so the line and the plan can never disagree, and a successor the
        selection already lists is not added twice. A successor that is itself
        retired is expanded in turn, which is how a second retirement of the same
        work still lands.
    #>
    param(
        [Parameter(Mandatory)][AllowEmptyCollection()][object[]]$Available,
        [Parameter(Mandatory)][AllowEmptyCollection()][string[]]$SelectedIds
    )

    $byId = @{}
    foreach ($c in $Available) { if (-not $byId.ContainsKey($c.Id)) { $byId[$c.Id] = $c } }

    $seen = @{}
    $queue = New-Object System.Collections.Queue
    foreach ($id in $SelectedIds) {
        if (-not $id -or $seen.ContainsKey($id)) { continue }
        $seen[$id] = $true
        [void]$queue.Enqueue($id)
    }

    $ids   = New-Object System.Collections.ArrayList
    $lines = New-Object System.Collections.ArrayList
    while ($queue.Count -gt 0) {
        $id = $queue.Dequeue()
        [void]$ids.Add($id)
        if (-not $byId.ContainsKey($id)) { continue }
        $c = $byId[$id]
        if (-not (Test-AutoOSTombstone -Component $c)) { continue }
        $named = @()
        foreach ($r in @(Get-AutoOSTombstoneReplacements -Component $c)) {
            if ($seen.ContainsKey($r)) { $named += $r; continue }
            if (-not $byId.ContainsKey($r)) { continue }   # not offered on this machine
            $seen[$r] = $true
            [void]$queue.Enqueue($r)
            $named += $r
        }
        if (-not $named.Count) { continue }
        [void]$lines.Add((Format-AutoOSTombstoneReplacement -Component $c -Replacements $named))
    }

    [pscustomobject]@{ Ids = @($ids); Lines = @($lines) }
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
                # 'none' means the component installs a background service with
                # no command to run, so the report says that rather than hunting
                # for a launcher and reporting it as missing.
                Launcher    = Get-AutoOSComponentProperty $c 'launcher' $null
                InstalledNames = @(Get-AutoOSComponentProperty $c 'installedNames' @())
                InstalledAppx = @(Get-AutoOSComponentProperty $c 'installedAppx' @())
                MinimumVersion = Get-AutoOSComponentProperty $c 'minimumVersion' $null
                Notes       = Get-AutoOSComponentProperty $c 'notes' $null
                Tombstone   = (Test-AutoOSTombstone -Component $c)
                RetireNote  = Get-AutoOSComponentProperty $c 'note' $null
                ReplacedBy  = @(Get-AutoOSComponentProperty $c 'replaced_by' @())
                Category    = $cat.name
                CategoryId  = $cat.id
            })
        }
    }
    $out
}

function Test-AutoOSProfileDefault {
    <#
      .SYNOPSIS Would the chosen profile tick this component by default?
      .DESCRIPTION A tombstone is never ticked: its id stays published so saved
        selections resolve, but a fresh profile run must not plan - or pay for -
        something that installs nothing.
    #>
    param(
        [Parameter(Mandatory)][psobject]$Component,
        # Not $Profile: that is a PowerShell automatic variable ($PROFILE).
        [string]$ProfileName = 'custom'
    )
    $Component.Provider -ne 'manual' -and
        -not (Test-AutoOSTombstone -Component $Component) -and
        $ProfileName -ne 'custom' -and
        ($ProfileName -in @(Get-AutoOSComponentProperty $Component 'Profiles' @()))
}

function Get-AutoOSProfileDefaults {
    <#
      .SYNOPSIS The ids a profile pre-selects on this machine.
      .DESCRIPTION Used by the non-interactive -Yes path; the menu asks the same
        question per row through New-AutoOSMenuItem.
    #>
    param(
        [Parameter(Mandatory)][AllowEmptyCollection()][object[]]$Available,
        [string]$ProfileName = 'custom'
    )
    @($Available | Where-Object { Test-AutoOSProfileDefault -Component $_ -ProfileName $ProfileName } |
      ForEach-Object { $_.Id })
}

function New-AutoOSMenuItem {
    <#
      .SYNOPSIS Shape an available component into a row the menu understands.
    #>
    param(
        [Parameter(Mandatory)][psobject]$Component,
        # Not $Profile: that is a PowerShell automatic variable ($PROFILE).
        [Alias('Profile')][string]$ProfileName = 'custom',
        [bool]$Installed = $false
    )
    [pscustomobject]@{
        Id          = $Component.Id
        Name        = $Component.Name
        Description = $(if (Test-AutoOSTombstone -Component $Component) {
                           # The row is still offered - a user who remembers this
                           # product should learn from the row itself why picking
                           # it does nothing.
                           "$($Component.Description) (retired)"
                       } else { $Component.Description })
        Group       = $Component.Category
        Selected    = (Test-AutoOSProfileDefault -Component $Component -ProfileName $ProfileName)
        Locked      = ($Component.Provider -eq 'manual')
        Reason      = $(if ($Component.Provider -eq 'manual') { 'Vendor setup required; AutoOS cannot install this application.' } else { '' })
        Installed   = $Installed
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
        Ordered component objects, dependencies first. Each carries AutoAdded, and
        a BlockedReason that is empty unless resolve refused to install that row -
        the validator rejects a 'requires' that names a tombstone, but a normal run
        never validates, so the plan records it here and the entry point announces
        and fails it rather than installing the dependent without its dependency.
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
    $blockedIds = @{}
    foreach ($id in $SelectedIds) { if ($byId.ContainsKey($id)) { [void]$queue.Enqueue($id) } }
    while ($queue.Count -gt 0) {
        $id = $queue.Dequeue()
        if ($byId[$id].Provider -eq 'manual') { throw "AutoOS cannot install '$id'; use its vendor link for manual setup." }
        if (-not $wanted.Add($id)) { continue }
        # A retired id is chosen or replayed, never expanded: what it used to
        # require belongs to whatever replaced it, not to this row.
        if (Test-AutoOSTombstone -Component $byId[$id]) { continue }
        foreach ($dep in $byId[$id].Requires) {
            if ($byId.ContainsKey($dep)) {
                # The dependent is refused, but the retired row stays in the plan:
                # it is what the plan and the report both get to point at, and it
                # installs nothing either way.
                if (Test-AutoOSTombstone -Component $byId[$dep]) {
                    $blockedIds[$id] = "requires retired $dep"
                }
                [void]$queue.Enqueue($dep)
            }
        }
    }

    # A component that needed one of those refused components cannot be installed
    # either, and quietly dropping only the first would repeat the same defect one
    # level up - so the refusal spreads until the set stops growing.
    $grew = $true
    while ($grew) {
        $grew = $false
        foreach ($id in @($wanted)) {
            if ($blockedIds.ContainsKey($id)) { continue }
            if (Test-AutoOSTombstone -Component $byId[$id]) { continue }
            foreach ($dep in $byId[$id].Requires) {
                if ($blockedIds.ContainsKey($dep)) {
                    $blockedIds[$id] = "requires $dep, which cannot be installed"
                    $grew = $true
                    break
                }
            }
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
        Add-Member -InputObject $node -NotePropertyName BlockedReason `
                   -NotePropertyValue $(if ($blockedIds.ContainsKey($Id)) { $blockedIds[$Id] } else { '' }) -Force
        [void]$ordered.Add($node)
    }

    foreach ($id in $wanted) {
        Add-AutoOSTopoNode -Id $id -Path (New-Object System.Collections.Generic.List[string])
    }
    $ordered
}

Export-ModuleMember -Function `
    Get-AutoOSCatalog, Test-AutoOSCatalogSchema, Get-AutoOSAvailableComponents,
    New-AutoOSMenuItem, Resolve-AutoOSPlan, Get-AutoOSComponentProperty,
    Test-AutoOSTombstone, Get-AutoOSTombstoneNote, Format-AutoOSTombstoneSkip,
    Get-AutoOSTombstoneReplacements, Format-AutoOSTombstoneReplacement,
    Expand-AutoOSTombstoneReplacements, Get-AutoOSProfileDefaults
