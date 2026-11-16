# crear-acceso-directo.ps1
# Crea un acceso directo en el Escritorio que apunta a iniciar-nexora.bat.
# Ejecutar una sola vez (click derecho -> Ejecutar con PowerShell), o
# manualmente: powershell -ExecutionPolicy Bypass -File crear-acceso-directo.ps1

$ErrorActionPreference = "Stop"
$carpeta = $PSScriptRoot
$escritorio = [Environment]::GetFolderPath("Desktop")
$rutaAcceso = Join-Path $escritorio "NEXORA-CONTROL.lnk"

$shell = New-Object -ComObject WScript.Shell
$acceso = $shell.CreateShortcut($rutaAcceso)
$acceso.TargetPath = Join-Path $carpeta "iniciar-nexora.bat"
$acceso.WorkingDirectory = $carpeta
$acceso.WindowStyle = 1
$acceso.Description = "Enciende NEXORA-CONTROL (panel web + API de NEXCAM + bot de Telegram)"
# Sin icono propio todavía: usa el de cmd.exe. Cambia esta línea si agregas un .ico.
$acceso.IconLocation = "$env:SystemRoot\System32\shell32.dll,13"
$acceso.Save()

Write-Host "Acceso directo creado en el Escritorio: $rutaAcceso"
