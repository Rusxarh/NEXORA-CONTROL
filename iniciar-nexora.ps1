# iniciar-nexora.ps1
# Lanzador unico de NEXORA-CONTROL (panel web + API de NEXCAM + bot de
# Telegram, todo en el mismo proceso). No lo ejecutes directamente a mano:
# hazle doble clic a iniciar-nexora.bat, que es el que llama a este script.

$ErrorActionPreference = "Stop"
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
Set-Location $PSScriptRoot

Write-Host "========================================"
Write-Host "  NEXORA-CONTROL"
Write-Host "========================================"
Write-Host ""

# 1) Revisa el puerto 8000 -----------------------------------------------
$conexion = Get-NetTCPConnection -LocalPort 8000 -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1

if ($conexion) {
    $proc = Get-CimInstance Win32_Process -Filter "ProcessId=$($conexion.OwningProcess)" -ErrorAction SilentlyContinue
    $esNexora = $proc -and ($proc.Name -match '^python') -and ($proc.CommandLine -match 'main\.py|main:app')
    $esNodeViejo = $proc -and ($proc.Name -eq 'node.exe')

    if ($esNexora) {
        Write-Host "NEXORA-CONTROL ya está encendida (PID $($conexion.OwningProcess)). Solo abro el navegador."
        Start-Process "http://localhost:8000"
        Read-Host "Presiona ENTER para cerrar esta ventana (NEXORA sigue encendida en la otra)"
        exit 0
    }
    elseif ($esNodeViejo) {
        Write-Host "El puerto 8000 lo tenía el servidor de prueba viejo de NEXCAM (node.exe, PID $($conexion.OwningProcess)). Lo cierro..."
        Stop-Process -Id $conexion.OwningProcess -Force -ErrorAction SilentlyContinue
        Start-Sleep -Seconds 1
    }
    else {
        $nombre = if ($proc) { $proc.Name } else { "desconocido" }
        Write-Host ""
        Write-Host "El puerto 8000 ya lo está usando otro programa: $nombre (PID $($conexion.OwningProcess))."
        Write-Host "Cierra ese programa o libera el puerto 8000 e inténtalo de nuevo."
        Write-Host ""
        Read-Host "Presiona ENTER para cerrar esta ventana"
        exit 1
    }
}

# 2) Python del entorno virtual si existe, y dependencias -----------------
$python = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $python)) { $python = "python" }

& $python -c "import fastapi, uvicorn, jinja2, multipart" 2>$null
if ($LASTEXITCODE -ne 0) {
    Write-Host "Instalando dependencias (primera vez)..."
    & $python -m pip install -r (Join-Path $PSScriptRoot "requirements.txt")
}

# 3) Firewall: solo informa, nunca la crea sin permiso ---------------------
$regla = Get-NetFirewallRule -DisplayName "NEXORA-CONTROL*" -ErrorAction SilentlyContinue
if (-not $regla) {
    Write-Host ""
    Write-Host "Aviso: no encontré una regla de firewall de Windows para el puerto 8000."
    Write-Host "Si el teléfono NO logra conectarse por Wi-Fi, abre PowerShell COMO ADMINISTRADOR y ejecuta:"
    Write-Host '  New-NetFirewallRule -DisplayName "NEXORA-CONTROL" -Direction Inbound -Protocol TCP -LocalPort 8000 -Action Allow'
    Write-Host ""
}

# 4) Abre el navegador apenas el servidor responda, sin bloquear el arranque
Start-Job -ScriptBlock {
    for ($i = 0; $i -lt 30; $i++) {
        Start-Sleep -Seconds 1
        try {
            $r = Invoke-WebRequest -Uri "http://127.0.0.1:8000/" -MaximumRedirection 0 -UseBasicParsing -TimeoutSec 2 -SkipHttpErrorCheck -ErrorAction Stop
            if ($r.StatusCode -eq 303) {
                Start-Process "http://localhost:8000"
                return
            }
        }
        catch {}
    }
} | Out-Null

Write-Host ""
Write-Host "No cierres esta ventana: si la cierras, se apaga NEXORA-CONTROL."
Write-Host ""

# 5) Arranca el servidor EN ESTA MISMA VENTANA (bloqueante). main.py ya
#    imprime las direcciones de acceso y el estado/código de Telegram.
& $python main.py
