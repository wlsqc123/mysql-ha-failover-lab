$ErrorActionPreference = 'Stop'
Set-Location (Split-Path $PSScriptRoot -Parent)
function Compose {
    $output = & docker compose @args
    if ($LASTEXITCODE -ne 0) { throw "Compose failed: $($args -join ' ')" }
    return $output
}
function Snapshot([string]$HostName) {
    return ((Compose run --rm workload python -m workload.observe $HostName) -join "`n" | ConvertFrom-Json)
}
$before = Snapshot 'mysql-1'
$members = @($before.members)
if ($members.Count -ne 3 -or @($members | Where-Object MEMBER_STATE -ne 'ONLINE').Count) { throw 'Require three ONLINE members' }
$primaries = @($members | Where-Object MEMBER_ROLE -eq 'PRIMARY')
if ($primaries.Count -ne 1) { throw 'Require one PRIMARY' }
$target = $primaries[0].MEMBER_HOST
if ($target -notin @('mysql-1','mysql-2','mysql-3')) { throw 'Unexpected target' }
$observer = ($members | Where-Object MEMBER_HOST -ne $target | Select-Object -First 1).MEMBER_HOST
$container = Compose ps -q $target
$networks = (& docker inspect --format '{{json .NetworkSettings.Networks}}' $container) | ConvertFrom-Json
if ($LASTEXITCODE -ne 0 -or @($networks.PSObject.Properties).Count -ne 1) { throw 'Require one existing DB network' }
$network = @($networks.PSObject.Properties)[0].Name
if (!$network) { throw 'DB network name could not be resolved' }
$runId = 'partition-' + [DateTime]::UtcNow.ToString('yyyyMMddTHHmmssZ') + '-' + [Guid]::NewGuid().ToString('N').Substring(0, 8)
Compose run -d --name $runId workload python -m workload.runner --run-id $runId --requests 1600 --workers 4 --interval 0.05 --retry-budget 60 --retry-delay 0.5 | Out-Null
$directory = Join-Path $PWD "results/$runId"
$deadline = [DateTime]::UtcNow.AddSeconds(60)
do {
    Start-Sleep -Milliseconds 250
    $ackCount = 0
    if (Test-Path "$directory/events.jsonl") {
        $ackCount = @(Get-Content "$directory/events.jsonl" | Select-String '"event": "commit_ack"').Count
    }
} until ($ackCount -ge 20 -or [DateTime]::UtcNow -gt $deadline)
if ($ackCount -lt 20) { throw 'Workload not ready; no network change' }
$timeline = "$directory/cluster-events.jsonl"
function Record($Event) { $Event | ConvertTo-Json -Depth 12 -Compress | Add-Content $timeline -Encoding utf8 }
Record @{event='baseline'; time_utc=[DateTime]::UtcNow.ToString('o'); snapshot=$before; target=$target; network=$network}
$check = Snapshot $observer
if (@($check.members | Where-Object { $_.MEMBER_HOST -eq $target -and $_.MEMBER_ROLE -eq 'PRIMARY' -and $_.MEMBER_STATE -eq 'ONLINE' }).Count -ne 1) { throw 'Target role changed' }
$disconnected = $false
try {
    Record @{event='disconnect_requested'; time_utc=[DateTime]::UtcNow.ToString('o'); target=$target}
    & docker network disconnect $network $container
    if ($LASTEXITCODE -ne 0) { throw 'Network disconnect failed' }
    $disconnected = $true
    Record @{event='disconnect_confirmed'; time_utc=[DateTime]::UtcNow.ToString('o'); target=$target}
    & docker inspect --format '{{json .NetworkSettings.Networks}}' $container > "$directory/isolated-networks.json"
    if ($LASTEXITCODE -ne 0) { throw 'Cannot inspect isolated network' }
    $isolatedNetworks = Get-Content "$directory/isolated-networks.json" -Raw | ConvertFrom-Json
    if (@($isolatedNetworks.PSObject.Properties).Count -ne 0) { throw 'Target still has a Docker network endpoint' }
    Write-Output "Isolated $target on $network; results/$runId"
    $deadline = [DateTime]::UtcNow.AddSeconds(90)
    $newPrimary = $null
    do {
        $state = Snapshot $observer
        Record @{event='snapshot'; time_utc=[DateTime]::UtcNow.ToString('o'); snapshot=$state}
        $elected = @($state.members | Where-Object { $_.MEMBER_ROLE -eq 'PRIMARY' -and $_.MEMBER_STATE -eq 'ONLINE' -and $_.MEMBER_HOST -ne $target })
        if ($elected.Count -eq 1) { $newPrimary = $elected[0].MEMBER_HOST; break }
        Start-Sleep -Seconds 1
    } until ([DateTime]::UtcNow -gt $deadline)
    if (!$newPrimary) { throw 'No new PRIMARY observed in majority' }
    # This probe shares the isolated DB network namespace and uses localhost, not Router.
    & docker run --rm --network "container:$container" --env-file .env -e LAB_DB_HOST=127.0.0.1 -e LAB_DB_PORT=3306 `
        --mount "type=bind,source=$PWD,target=/lab,readonly" --mount "type=bind,source=$directory,target=/lab/results/$runId" `
        -w /lab mysql-ha-lab-workload:local python -m workload.isolated_probe "results/$runId" *> "$directory/isolated-probe.log"
    if ($LASTEXITCODE -ne 0) { throw 'Isolated probe failed to execute' }
    $deadline = [DateTime]::UtcNow.AddSeconds(180)
    do {
        $state = Snapshot $observer
        Record @{event='snapshot'; time_utc=[DateTime]::UtcNow.ToString('o'); snapshot=$state}
        $running = & docker inspect --format '{{.State.Running}}' $runId
        if ($LASTEXITCODE -ne 0) { throw 'Cannot inspect workload' }
        if ($running -eq 'false') { break }
        Start-Sleep -Seconds 1
    } until ([DateTime]::UtcNow -gt $deadline)
    if ($running -ne 'false') { throw 'Workload deadline exceeded' }
    & docker logs $runId *> "$directory/workload.log"
    $workloadExit = & docker inspect --format '{{.State.ExitCode}}' $runId
} catch {
    Record @{event='experiment_error'; time_utc=[DateTime]::UtcNow.ToString('o'); error=$_.Exception.Message}
    throw
} finally {
    if ($disconnected) {
        & docker network connect --alias $target $network $container
        if ($LASTEXITCODE -ne 0) { throw "Reconnect failed; reconnect $target to $network manually" }
        Record @{event='network_restored'; time_utc=[DateTime]::UtcNow.ToString('o'); target=$target}
    }
}
$deadline = [DateTime]::UtcNow.AddSeconds(90)
do {
    $state = Snapshot $observer
    Record @{event='restore_snapshot'; time_utc=[DateTime]::UtcNow.ToString('o'); snapshot=$state}
    $ready = @($state.members | Where-Object MEMBER_STATE -eq 'ONLINE').Count -eq 3
    if ($ready) { break }
    Start-Sleep -Seconds 1
} until ([DateTime]::UtcNow -gt $deadline)
& docker compose logs --no-color --tail 400 *> "$directory/services.log"
if (!$ready) { throw 'Network restored but cluster not healthy; inspect logs before AdminAPI rejoin' }
Compose run --rm workload python -m workload.observe $newPrimary --require-primary $newPrimary > "$directory/reference.json"
foreach ($hostName in @('mysql-1','mysql-2','mysql-3')) {
    Compose run --rm workload python -m workload.observe $hostName --reference "results/$runId/reference.json" --require-primary $newPrimary > "$directory/final-$hostName.json"
    & docker compose run --rm -e "LAB_DB_HOST=$hostName" -e LAB_DB_PORT=3306 workload python -m workload.verify "results/$runId" > "$directory/verification-$hostName.json"
    if ($LASTEXITCODE -notin @(0,1)) { throw 'Verification execution failed' }
    # A completed report may fail due to exhausted retries; still inspect every node.
    Get-Content "$directory/verification-$hostName.json" -Raw | ConvertFrom-Json | Out-Null
}
& docker compose run --rm workload python -m workload.analyze_partition $runId
$analysisExit = $LASTEXITCODE
if ($analysisExit -notin @(0,1)) { throw 'Analysis execution failed' }
& "$PSScriptRoot/lab.ps1" smoke *> "$directory/post-restore-smoke.log"
if ($workloadExit -ne '0' -or $analysisExit -ne 0) { throw "Experiment completed with failures; see results/$runId" }
