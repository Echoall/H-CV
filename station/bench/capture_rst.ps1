param([string]$ControlDir, [int]$OwnerPid, [guid]$RunId)

$ErrorActionPreference = 'Stop'
$pkt = Join-Path ([Environment]::SystemDirectory) 'pktmon.exe'
$privateDir = Join-Path ([Environment]::SystemDirectory) "..\Temp\hcv-rst-$RunId"
$clock = [Diagnostics.Stopwatch]::StartNew()
$started = $false
$ownFilters = $null
function Active {
    return ($clock.Elapsed.TotalMinutes -lt 18 -and
            (Get-Process -Id $OwnerPid -ErrorAction SilentlyContinue) -and
            -not (Test-Path -LiteralPath (Join-Path $ControlDir 'finish')))
}
function State([string]$name) {
    [IO.File]::WriteAllText((Join-Path $privateDir 'state'), $name)
}
try {
    New-Item -Path $privateDir -ItemType Directory -ErrorAction Stop | Out-Null
    $status = (& $pkt status | Out-String).Trim()
    if ($LASTEXITCODE -ne 0 -or $status -notmatch 'not running|未运行|没有运行|未在运行') {
        State 'pktmon_busy_or_status_unknown'
        return
    }
    $filters = (& $pkt filter list | Out-String).Trim()
    if ($LASTEXITCODE -ne 0 -or $filters -notmatch 'no.*filters|未.*筛选器|没有.*筛选器|无.*筛选器' -or $filters -match '(?m)^\s*\d+') {
        State 'existing_or_unknown_filters'
        return
    }
    State 'ready'
    $request = Join-Path $ControlDir 'peer'
    while ((Active) -and -not (Test-Path -LiteralPath $request)) { Start-Sleep -Milliseconds 150 }
    if (-not (Active)) { return }
    $peer = [ipaddress]::Parse([IO.File]::ReadAllText($request))
    if ($peer.AddressFamily -ne 'InterNetwork' -or $peer.Equals([ipaddress]::Any)) { throw 'invalid peer' }
    foreach ($port in @(554,8554)) {
        $filterName = 'HCV-' + $RunId.ToString('N').Substring(0, 8) + "-$port"
        & $pkt filter add $filterName -i $peer.IPAddressToString -t TCP RST -p $port | Out-Null
        if ($LASTEXITCODE -ne 0) { throw 'filter failed' }
        $ownFilters = (& $pkt filter list | Out-String).Trim()
    }
    $etl = Join-Path $privateDir 'reset.etl'
    & $pkt start --capture --comp nics --pkt-size 96 --file-size 16 --log-mode circular --file-name $etl | Out-Null
    if ($LASTEXITCODE -ne 0) { throw 'start failed' }
    $started = $true
    State 'capturing'
    while (Active) { Start-Sleep -Milliseconds 300 }
} catch {
    if (Test-Path -LiteralPath $privateDir) { State 'helper_error' }
} finally {
    if ($started) {
        & $pkt stop | Out-Null
        if ($LASTEXITCODE -eq 0) {
            & $pkt etl2pcap $etl --out (Join-Path $privateDir 'reset.pcapng') | Out-Null
            if ($LASTEXITCODE -eq 0) { State 'complete' } else { State 'conversion_failed' }
        } else { State 'stop_failed' }
    }
    if ($ownFilters -and (& $pkt filter list | Out-String).Trim() -ceq $ownFilters) {
        & $pkt filter remove | Out-Null
    }
}
