param([ValidateSet('up','status','smoke','rewards','stop','logs')][string]$Action = 'status')
$ErrorActionPreference = 'Stop'
Set-Location (Split-Path $PSScriptRoot -Parent)
function Compose {
    & docker compose @args
    if ($LASTEXITCODE -ne 0) { throw "Docker Compose failed (exit $LASTEXITCODE). Inspect: docker compose logs" }
}
if (!(Test-Path -LiteralPath '.env')) { throw 'Copy .env.example to .env and set local passwords first.' }
switch ($Action) {
    'up' {
        Compose up -d --wait --wait-timeout 300 mysql-1 mysql-2 mysql-3
        Compose run --rm admin
        Compose up -d router
        Compose run --rm -e LAB_MODE=smoke admin
    }
    'status' { Compose run --rm -e LAB_MODE=status admin }
    'rewards' {
        Compose build workload
        Compose run --rm workload python -m workload.setup
        Compose run --rm workload
    }
    'smoke' { Compose run --rm -e LAB_MODE=smoke admin }
    'stop' { Compose stop }
    'logs' { Compose logs --no-color --tail 200 }
}
