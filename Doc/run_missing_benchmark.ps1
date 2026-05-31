#Requires -Version 5.1
<#
.SYNOPSIS
  Eksik 5 metot (ga, tabu, gatabu, ga_tabu_inline, ga_tabu_topk) icin NP=20 benchmark
  kosusunu baslatir, biter bitmez Excel tablolarini yeniden uretir.

.DESCRIPTION
  1) API yoksa arka planda uvicorn baslatir (127.0.0.1:8000)
  2) Doc/api_benchmark_runner.py + Doc/benchmark_test_params.json
  3) Cikti: Doc/benchmark_outputs/paper_comparison_main_3/
  4) Doc/build_tables_xlsx.py -> Doc/benchmark_outputs/tables_report.xlsx
     (paper_comparison_main_2 ile otomatik birlestirilir)

  Tahmini sure: ~4-6 saat (900 is). Kesilirse resume=true ile ayni komutu tekrar calistir.

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File Doc\run_missing_benchmark.ps1
#>

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
Set-Location $RepoRoot

$ConfigPath = Join-Path $RepoRoot "Doc\benchmark_test_params.json"
$RunnerPath = Join-Path $RepoRoot "Doc\api_benchmark_runner.py"
$ExcelScript = Join-Path $RepoRoot "Doc\build_tables_xlsx.py"
$LogPath = Join-Path $RepoRoot "Doc\benchmark_outputs\paper_comparison_main_3_run.log"
$RunOutDir = Join-Path $RepoRoot "Doc\benchmark_outputs\paper_comparison_main_3"
$ApiUrl = "http://127.0.0.1:8000"
$HealthUrl = "$ApiUrl/health"

function Write-Log([string]$Message) {
    $line = "[{0}] {1}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $Message
    Write-Host $line
    $logDir = Split-Path $LogPath -Parent
    if (-not (Test-Path $logDir)) {
        New-Item -ItemType Directory -Path $logDir -Force | Out-Null
    }
    Add-Content -Path $LogPath -Value $line -Encoding UTF8
}

function Resolve-PythonExe {
    $candidates = @(
        (Join-Path $RepoRoot ".venv\Scripts\python.exe"),
        (Join-Path $RepoRoot "tests\.venv\Scripts\python.exe"),
        "C:\Users\Artun\anaconda3\python.exe"
    )
    foreach ($c in $candidates) {
        if ($c -and (Test-Path $c)) { return (Resolve-Path $c).Path }
    }
    $py = Get-Command python -ErrorAction SilentlyContinue
    if ($py) { return $py.Source }
    throw "Python bulunamadi. .venv olusturun veya PATH'e python ekleyin."
}

function Test-ApiHealthy {
    try {
        $resp = Invoke-WebRequest -Uri $HealthUrl -UseBasicParsing -TimeoutSec 8
        return ($resp.StatusCode -eq 200)
    } catch {
        return $false
    }
}

function Wait-ApiHealthy([int]$MaxWaitSec = 120) {
    $deadline = (Get-Date).AddSeconds($MaxWaitSec)
    while ((Get-Date) -lt $deadline) {
        if (Test-ApiHealthy) { return }
        Start-Sleep -Seconds 2
    }
    throw "API $HealthUrl hazir olmadi (${MaxWaitSec}s). Log: $LogPath"
}

function Invoke-PythonChecked(
    [string]$PythonExe,
    [string[]]$ArgumentList,
    [string]$StepLabel
) {
    # pip/python often write notices to stderr; with $ErrorActionPreference Stop
    # PowerShell treats that as a terminating error even when exit code is 0.
    $prevEap = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    $prevUnbuf = $env:PYTHONUNBUFFERED
    $env:PYTHONUNBUFFERED = "1"
    try {
        & $PythonExe @ArgumentList 2>&1 | ForEach-Object {
            $line = if ($_ -is [System.Management.Automation.ErrorRecord]) {
                $_.Exception.Message
            } else {
                [string]$_
            }
            if ($line.Trim()) {
                Write-Host $line
                Add-Content -Path $LogPath -Value $line -Encoding UTF8
            }
        }
        if ($LASTEXITCODE -ne 0) {
            throw ("{0} basarisiz (exit code {1})" -f $StepLabel, $LASTEXITCODE)
        }
    } finally {
        $ErrorActionPreference = $prevEap
        if ($null -eq $prevUnbuf) {
            Remove-Item Env:PYTHONUNBUFFERED -ErrorAction SilentlyContinue
        } else {
            $env:PYTHONUNBUFFERED = $prevUnbuf
        }
    }
}

function Ensure-Dependencies([string]$PythonExe) {
    Write-Log "Bagimliliklar kontrol ediliyor..."
    $prevEap = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    & $PythonExe -c "import fastapi, uvicorn, openpyxl, matplotlib" 2>$null | Out-Null
    $depsOk = ($LASTEXITCODE -eq 0)
    $ErrorActionPreference = $prevEap
    if ($depsOk) {
        Write-Log "Bagimliliklar zaten kurulu (pip atlandi)."
        return
    }
    Invoke-PythonChecked -PythonExe $PythonExe `
        -ArgumentList @("-m", "pip", "install", "-q", "-r", "requirements.txt") `
        -StepLabel "pip install requirements.txt"
    Invoke-PythonChecked -PythonExe $PythonExe `
        -ArgumentList @("-m", "pip", "install", "-q", "openpyxl", "matplotlib") `
        -StepLabel "pip install openpyxl matplotlib"
    Write-Log "Bagimliliklar tamam."
}

$startedApiProcess = $false
$apiProcess = $null
$benchmarkSucceeded = $false
$CheckpointPath = Join-Path $RunOutDir "state\checkpoint.json"

function Show-ResumeStatus {
    $CsvPath = Join-Path $RunOutDir "csv\run_results.csv"
    if (Test-Path $CsvPath) {
        try {
            $rows = Import-Csv $CsvPath
            $ok = ($rows | Where-Object { $_.status -eq "ok" }).Count
            $err = ($rows | Where-Object { $_.status -eq "error" }).Count
            Write-Log ("CSV: {0} basarili (ok), {1} hata (error), toplam satir {2}." -f $ok, $err, $rows.Count)
        } catch {
            Write-Log "CSV durumu okunamadi."
        }
    }
    if (-not (Test-Path $CheckpointPath)) {
        Write-Log "Resume: yeni kosu (checkpoint yok)."
        return
    }
    try {
        $cp = Get-Content $CheckpointPath -Raw | ConvertFrom-Json
        Write-Log ("Checkpoint: {0}/{1} is tamamlandi, kalan {2}." -f `
            $cp.completed_jobs, $cp.total_jobs, $cp.remaining_jobs)
        if ($cp.last_completed_job) {
            Write-Log ("Son basarili: {0}" -f $cp.last_completed_job.job_key)
        }
        if ($cp.current_job) {
            Write-Log ("Yarida kalan: {0}" -f $cp.current_job.job_key)
        }
    } catch {
        Write-Log "Resume: checkpoint okunamadi."
    }
}

function Clear-CheckpointFrameId {
    param([string]$Reason)
    if (-not (Test-Path $CheckpointPath)) { return }
    try {
        $raw = Get-Content $CheckpointPath -Raw | ConvertFrom-Json
        if ($raw.frame_id) {
            Write-Log ("Checkpoint frame_id temizlendi ({0})." -f $Reason)
            $raw.frame_id = ""
            $raw | ConvertTo-Json -Depth 8 | Set-Content -Path $CheckpointPath -Encoding UTF8
        }
    } catch {
        Write-Log "Checkpoint frame_id temizlenemedi (sorun degil, runner yeni frame acar)."
    }
}

try {
    Write-Log "=== Eksik metot benchmark basladi ==="
    Write-Log "Repo: $RepoRoot"

    $Python = Resolve-PythonExe
    Write-Log "Python: $Python"

    Ensure-Dependencies -PythonExe $Python

    if (-not (Test-Path $ConfigPath)) {
        throw "Config yok: $ConfigPath"
    }

    $cfg = Get-Content $ConfigPath -Raw | ConvertFrom-Json
    Write-Log ("Run name: {0}" -f $cfg.benchmark_plan.run_name)
    Write-Log ("Methods: {0}" -f ($cfg.algorithm_selection.include_methods -join ", "))
    Write-Log ("Output: {0}" -f $cfg.input_output.output_dir)
    Write-Log ("NP (population_size): {0}" -f $cfg.parameters.ui_global_parameters.ga_and_hybrids.population_size)
    Write-Log ("n_iter: {0}" -f ($cfg.benchmark_plan.iteration_list -join ", "))
    Write-Log ("Jobs: {0} metot x {1} n_iter x {2} run = {3}" -f `
        $cfg.algorithm_selection.include_methods.Count, `
        $cfg.benchmark_plan.iteration_list.Count, `
        $cfg.benchmark_plan.runs_per_iteration, `
        ($cfg.algorithm_selection.include_methods.Count * $cfg.benchmark_plan.iteration_list.Count * $cfg.benchmark_plan.runs_per_iteration))
    $timeoutCfg = $cfg.api.timeout_seconds
    if ($timeoutCfg -le 0) {
        Write-Log "API timeout: sinirsiz (optimize istekleri bitene kadar bekler)."
    } else {
        Write-Log ("API timeout (saniye): {0}" -f $timeoutCfg)
    }

    Show-ResumeStatus

    if (Test-ApiHealthy) {
        Write-Log "API zaten calisiyor ($HealthUrl) - kapatma, resume devam eder."
    } else {
        Write-Log "API baslatiliyor (uvicorn)..."
        Clear-CheckpointFrameId -Reason "yeni API oturumu"
        $apiLogOut = Join-Path $RepoRoot "Doc\benchmark_outputs\api_server.log"
        $apiLogErr = Join-Path $RepoRoot "Doc\benchmark_outputs\api_server.err"
        $apiProcess = Start-Process `
            -FilePath $Python `
            -ArgumentList @("-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", "8000") `
            -WorkingDirectory $RepoRoot `
            -PassThru `
            -WindowStyle Hidden `
            -RedirectStandardOutput $apiLogOut `
            -RedirectStandardError $apiLogErr
        $startedApiProcess = $true
        Wait-ApiHealthy -MaxWaitSec 240
        Start-Sleep -Seconds 3
        if (-not (Test-ApiHealthy)) {
            throw "API health check ikinci denemede de basarisiz."
        }
        Write-Log "API hazir (PID $($apiProcess.Id))."
    }

    $benchStart = Get-Date
    $timeoutSec = $cfg.api.timeout_seconds
    if ($timeoutSec -le 0) {
        Write-Log "Benchmark kosusu basliyor (resume=true, timeout=sinirsiz)..."
    } else {
        Write-Log "Benchmark kosusu basliyor (resume=true, timeout=$timeoutSec saniye)..."
    }
    Invoke-PythonChecked -PythonExe $Python `
        -ArgumentList @($RunnerPath, "--config", $ConfigPath) `
        -StepLabel "api_benchmark_runner"
    $benchElapsed = (Get-Date) - $benchStart
    Write-Log ("Benchmark tamamlandi. Sure: {0:hh\:mm\:ss}" -f $benchElapsed)

    if (-not (Test-Path (Join-Path $RunOutDir "manifest.json"))) {
        throw "Beklenen cikti yok: $RunOutDir\manifest.json"
    }

    Write-Log "Excel tablolari birlestiriliyor (main_2 + main_3)..."
    Invoke-PythonChecked -PythonExe $Python `
        -ArgumentList @($ExcelScript) `
        -StepLabel "build_tables_xlsx"

    $xlsx = Join-Path $RepoRoot "Doc\benchmark_outputs\tables_report.xlsx"
    Write-Log "=== TAMAMLANDI ==="
    Write-Log "Benchmark CSV/plots: $RunOutDir"
    Write-Log "Excel rapor: $xlsx"
    Write-Log "Log: $LogPath"
    Write-Host ""
    Write-Host "Bitti. Excel: $xlsx" -ForegroundColor Green
    $benchmarkSucceeded = $true
}
catch {
    $errMsg = $_.Exception.Message
    if ([string]::IsNullOrWhiteSpace($errMsg)) {
        $errMsg = $_.ToString()
    }
    if ($_.InvocationInfo) {
        $errMsg += (' @ ' + $_.InvocationInfo.ScriptName + ':' + $_.InvocationInfo.ScriptLineNumber)
    }
    Write-Log "HATA: $errMsg"
    Write-Host $errMsg -ForegroundColor Red
    Write-Host "Detay log: $LogPath" -ForegroundColor Yellow
    Write-Host "Benchmark yarida kaldiysa AYNI komutu tekrar calistir (resume=true)." -ForegroundColor Yellow
    Write-Host "API kapatilmadi - tekrar calistirinca kaldigi yerden devam eder." -ForegroundColor Yellow
    exit 1
}
finally {
    # API'yi yalnizca benchmark TAM bittiginde kapat; aksi halde resume bozulur ve timed out olur.
    if ($benchmarkSucceeded -and $startedApiProcess -and $apiProcess -and -not $apiProcess.HasExited) {
        Write-Log "Benchmark bitti; bu oturumda baslatilan API durduruluyor (PID $($apiProcess.Id))..."
        Stop-Process -Id $apiProcess.Id -Force -ErrorAction SilentlyContinue
    } elseif ($startedApiProcess -and $apiProcess -and -not $apiProcess.HasExited) {
        Write-Log "API acik birakildi (PID $($apiProcess.Id)) - resume icin ayni komutu tekrar calistir."
    }
}

exit 0
