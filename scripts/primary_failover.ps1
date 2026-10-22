$ErrorActionPreference = 'Stop'
Set-Location (Split-Path $PSScriptRoot -Parent)
function Compose {
    $output = & docker compose @args
    if ($LASTEXITCODE -ne 0) { throw "Compose failed: $($args -join ' ')" }
    return $output
}
function Snapshot([string]$HostName) {
    $json = Compose run --rm workload python -m workload.observe $HostName
    return ($json -join "`n" | ConvertFrom-Json)
}
$before = Snapshot 'mysql-1'
$members = @($before.members)
if ($members.Count -ne 3 -or @($members | Where-Object MEMBER_STATE -ne 'ONLINE').Count -ne 0) {
    throw 'Experiment requires 3 ONLINE members. No fault injected.'
}
$primary = @($members | Where-Object MEMBER_ROLE -eq 'PRIMARY')
if ($primary.Count -ne 1) { throw 'Expected exactly one PRIMARY' }
$target = $primary[0].MEMBER_HOST
if ($target -notin @('mysql-1', 'mysql-2', 'mysql-3')) { throw 'Unexpected target host' }
$observer = ($members | Where-Object MEMBER_HOST -ne $target | Select-Object -First 1).MEMBER_HOST
$container = Compose ps -q $target
if (!$container) { throw 'Primary container not found' }
$policy = & docker inspect --format '{{.HostConfig.RestartPolicy.Name}}' $container
if ($LASTEXITCODE -ne 0 -or $policy -ne 'no') { throw 'Primary automatic restart must be disabled' }
$runId = 'primary-' + [DateTime]::UtcNow.ToString('yyyyMMddTHHmmssZ') + '-' + [Guid]::NewGuid().ToString('N').Substring(0, 8)
Compose run -d --name $runId workload python -m workload.runner --run-id $runId --requests 1600 --workers 4 --interval 0.05 --max-attempts 40 --retry-delay 0.5 | Out-Null
$directory = Join-Path $PWD "results/$runId"
$eventPath = Join-Path $directory 'events.jsonl'
$deadline = [DateTime]::UtcNow.AddSeconds(60)
do {
    Start-Sleep -Milliseconds 250
    $acks = 0
    if (Test-Path -LiteralPath $eventPath) {
        $acks = @(Get-Content -LiteralPath $eventPath | Select-String '"event": "commit_ack"').Count
    }
} until ($acks -ge 20 -or [DateTime]::UtcNow -gt $deadline)
if ($acks -lt 20) { throw "Workload readiness failed; no fault injected. Inspect container $runId" }

$timeline = Join-Path $directory 'cluster-events.jsonl'
function Record($Event) {
    $Event | ConvertTo-Json -Depth 12 -Compress | Add-Content -LiteralPath $timeline -Encoding utf8
}
Record @{event='baseline'; time_utc=[DateTime]::UtcNow.ToString('o'); snapshot=$before}
# Recheck role immediately before injection; never assume the original role is unchanged.
$check = Snapshot $observer
$currentPrimary = @($check.members | Where-Object { $_.MEMBER_ROLE -eq 'PRIMARY' -and $_.MEMBER_STATE -eq 'ONLINE' })
if ($currentPrimary.Count -ne 1 -or $currentPrimary[0].MEMBER_HOST -ne $target) { throw 'Primary changed before injection; no fault injected' }
Record @{event='kill_requested'; time_utc=[DateTime]::UtcNow.ToString('o'); target=$target; container=$container}
Compose kill -s SIGKILL $target | Out-Null
Record @{event='kill_confirmed'; time_utc=[DateTime]::UtcNow.ToString('o'); target=$target}
Write-Output "SIGKILL sent to $target. Observing through $observer; results/$runId"
$deadline = [DateTime]::UtcNow.AddSeconds(180)
do {
    try {
        $snapshot = Snapshot $observer
        Record @{event='snapshot'; time_utc=[DateTime]::UtcNow.ToString('o'); snapshot=$snapshot}
    } catch {
        Record @{event='observation_error'; time_utc=[DateTime]::UtcNow.ToString('o'); error=$_.Exception.Message}
    }
    $running = & docker inspect --format '{{.State.Running}}' $runId
    if ($LASTEXITCODE -ne 0) { throw 'Cannot inspect workload container' }
    if ($running -eq 'false') { break }
    Start-Sleep -Seconds 1
} until ([DateTime]::UtcNow -gt $deadline)
if ($running -ne 'false') { throw "Workload deadline exceeded. Inspect $runId; nodes left unchanged." }
$exitCode = & docker inspect --format '{{.State.ExitCode}}' $runId
& docker logs $runId *> (Join-Path $directory 'workload.log')
& docker compose logs --no-color --tail 300 *> (Join-Path $directory 'services.log')
foreach ($member in ($members | Where-Object MEMBER_HOST -ne $target)) {
    $hostName = $member.MEMBER_HOST
    Snapshot $hostName | ConvertTo-Json -Depth 12 | Set-Content -LiteralPath (Join-Path $directory "final-$hostName.json") -Encoding utf8
    Compose run --rm -e "LAB_DB_HOST=$hostName" -e LAB_DB_PORT=3306 workload python -m workload.verify "results/$runId" |
        Set-Content -LiteralPath (Join-Path $directory "verification-$hostName.json") -Encoding utf8
}
foreach ($member in ($members | Where-Object MEMBER_HOST -ne $target)) {
    $hostName = $member.MEMBER_HOST
    Compose run --rm workload python -m workload.observe $hostName --reference "results/$runId/final-$observer.json" |
        Set-Content -LiteralPath (Join-Path $directory "gtid-check-$hostName.json") -Encoding utf8
}
Compose run --rm workload python -m workload.analyze_failover $runId
if ($exitCode -ne '0') { throw "Workload failed ($exitCode). See results/$runId" }
# Retain the stopped workload container and killed DB for inspection; no node restart here.
