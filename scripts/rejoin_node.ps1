param(
    [Parameter(Mandatory=$true)][ValidateSet('mysql-1','mysql-2','mysql-3')][string]$Node,
    [Parameter(Mandatory=$true)][ValidateSet('mysql-1','mysql-2','mysql-3')][string]$Observer,
    [Parameter(Mandatory=$true)][ValidatePattern('^[A-Za-z0-9_-]+$')][string]$WorkloadRun
)
$ErrorActionPreference = 'Stop'
Set-Location (Split-Path $PSScriptRoot -Parent)
function Compose {
    $output = & docker compose @args
    if ($LASTEXITCODE -ne 0) { throw "Compose failed: $($args -join ' ')" }
    return $output
}
if ($Node -eq $Observer) { throw 'Observer must be a surviving node' }
if (!(Test-Path -LiteralPath "results/$WorkloadRun/manifest.json")) { throw 'Workload manifest not found' }
$container = Compose ps -a -q $Node
if (!$container) { throw 'Target container not found' }
$running = & docker inspect --format '{{.State.Running}}' $container
if ($LASTEXITCODE -ne 0 -or $running -ne 'false') { throw 'Target must be a stopped existing node' }
$before = (Compose run --rm workload python -m workload.observe $Observer) -join "`n" | ConvertFrom-Json
$online = @($before.members | Where-Object MEMBER_STATE -eq 'ONLINE')
$primary = @($online | Where-Object MEMBER_ROLE -eq 'PRIMARY')
if ($online.Count -ne 2 -or $primary.Count -ne 1 -or $Node -in $online.MEMBER_HOST) {
    throw 'Expected two ONLINE survivors and one PRIMARY; no restart performed'
}
$expectedPrimary = $primary[0].MEMBER_HOST
$runId = 'rejoin-' + [DateTime]::UtcNow.ToString('yyyyMMddTHHmmssZ') + '-' + [Guid]::NewGuid().ToString('N').Substring(0, 8)
$directory = Join-Path $PWD "results/$runId"
New-Item -ItemType Directory -Path $directory | Out-Null
$before | ConvertTo-Json -Depth 12 | Set-Content "$directory/before.json" -Encoding utf8
$started = [DateTime]::UtcNow
@{event='restart_requested'; node=$Node; time_utc=$started.ToString('o'); workload_run=$WorkloadRun} |
    ConvertTo-Json -Compress | Set-Content "$directory/events.jsonl" -Encoding utf8
Compose start $Node | Out-Null
$deadline = [DateTime]::UtcNow.AddSeconds(90)
$ready = $false
do {
    $state = (Compose run --rm workload python -m workload.observe $Observer) -join "`n" | ConvertFrom-Json
    @{event='snapshot'; time_utc=[DateTime]::UtcNow.ToString('o'); snapshot=$state} |
        ConvertTo-Json -Depth 12 -Compress | Add-Content "$directory/events.jsonl" -Encoding utf8
    $ready = @($state.members | Where-Object MEMBER_STATE -eq 'ONLINE').Count -eq 3
    if ($ready) { break }
    Start-Sleep -Seconds 1
} until ([DateTime]::UtcNow -gt $deadline)
& docker compose logs --no-color --since $started.ToString('o') $Node *> "$directory/recovery.log"
if (!$ready) { throw "Auto-rejoin did not finish. See results/$runId; no forced recovery performed." }
$onlineObserved = [DateTime]::UtcNow
Compose run --rm workload python -m workload.observe $expectedPrimary --require-primary $expectedPrimary |
    Set-Content "$directory/reference.json" -Encoding utf8
foreach ($hostName in @('mysql-1','mysql-2','mysql-3')) {
    Compose run --rm workload python -m workload.observe $hostName --reference "results/$runId/reference.json" --require-primary $expectedPrimary |
        Set-Content "$directory/final-$hostName.json" -Encoding utf8
    Compose run --rm -e "LAB_DB_HOST=$hostName" -e LAB_DB_PORT=3306 workload python -m workload.verify "results/$WorkloadRun" |
        Set-Content "$directory/verification-$hostName.json" -Encoding utf8
}
$summary = @{result='PASS'; node=$Node; recovery='automatic'; primary=$expectedPrimary;
    online_observed_seconds=($onlineObserved - $started).TotalSeconds; workload_run=$WorkloadRun;
    verified_nodes=@('mysql-1','mysql-2','mysql-3')}
$summary | ConvertTo-Json -Depth 6 | Set-Content "$directory/summary.json" -Encoding utf8
Write-Output "Results: results/$runId"
$summary | ConvertTo-Json -Depth 6
