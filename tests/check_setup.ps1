$ErrorActionPreference = 'Stop'
Set-Location (Split-Path $PSScriptRoot -Parent)
$config = & docker compose config --format json | ConvertFrom-Json
if ($LASTEXITCODE -ne 0) { throw 'Compose configuration invalid' }
$dbNames = @('mysql-1', 'mysql-2', 'mysql-3')
$sources = @()
foreach ($name in $dbNames) {
    $service = $config.services.$name
    if ($service.restart -ne 'no') { throw "$name must not restart during fault tests" }
    if ($service.ports[0].host_ip -ne '127.0.0.1') { throw "$name management port exposed" }
    $sources += $service.volumes[0].source
    if ($service.command -notcontains '--gtid-mode=ON') { throw "$name missing GTID" }
}
if (($sources | Select-Object -Unique).Count -ne 3) { throw 'DB volumes must be independent' }
foreach ($service in $config.services.PSObject.Properties.Value) {
    if ($service.build) { continue }
    if ($service.image -notmatch ':8\.4\.0$') { throw 'Image version not pinned' }
}
$parseErrors = $null
$tokens = $null
[System.Management.Automation.Language.Parser]::ParseFile((Join-Path $PWD 'scripts/lab.ps1'), [ref]$tokens, [ref]$parseErrors) | Out-Null
if ($parseErrors.Count) { throw ($parseErrors | Out-String) }
Write-Output 'PASS: Compose, version pins, independent volumes, local ports, GTID, restart policy, PowerShell syntax'
