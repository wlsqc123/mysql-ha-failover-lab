$ErrorActionPreference = 'Stop'
Set-Location (Split-Path $PSScriptRoot -Parent)
$runId = 'diagnostics-' + [DateTime]::UtcNow.ToString('yyyyMMddTHHmmssZ')
$directory = Join-Path $PWD "results/$runId"
New-Item -ItemType Directory -Path $directory | Out-Null
& docker network inspect mysql-ha-failover-lab_lab > "$directory/docker-network.json"
if ($LASTEXITCODE -ne 0) { throw 'Cannot inspect lab network' }
foreach ($node in @('mysql-1','mysql-2','mysql-3')) {
    $container = & docker compose ps -q $node
    if ($LASTEXITCODE -ne 0 -or !$container) { throw "$node container not found" }
    & docker run --rm --network "container:$container" --env-file .env `
        --mount "type=bind,source=$PWD,target=/lab,readonly" -w /lab mysql-ha-lab-workload:local `
        python -m workload.network_diagnostics $node > "$directory/$node.json"
    if ($LASTEXITCODE -ne 0) { throw "$node peer checks failed; see results/$runId" }
    $report = Get-Content "$directory/$node.json" -Raw | ConvertFrom-Json
    $report.peers | ForEach-Object {
        [PSCustomObject]@{Source=$node; Target=$_.target; Address=$_.remote_address;
            TCP_ms=$_.tcp_connect_ms; RecoveryAuth=$_.recovery_authentication}
    } | Format-Table
}
& docker compose logs --no-color --since 2026-10-22T11:46:10Z > "$directory/post-restore-services.log"
Write-Output "Current peer checks passed; historical root cause is not confirmed. Results: results/$runId"
