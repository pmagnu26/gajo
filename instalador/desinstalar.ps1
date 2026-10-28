# Desinstalador de Gajo: cierra la app, quita sus accesos directos y borra la carpeta del programa.
# No desinstala Python, ffmpeg, Pandoc ni LibreOffice (otros programas pueden usarlos).
param(
    [string]$Destino = (Join-Path $env:LOCALAPPDATA "Programs\Gajo"),
    [switch]$Silencioso   # no pregunta; conserva la configuracion
)
$ErrorActionPreference = "Continue"
$pyw = (Join-Path $Destino "gajo.pyw").ToLower()

Get-CimInstance Win32_Process -Filter "Name='pythonw.exe'" -ErrorAction SilentlyContinue |
    Where-Object { $_.CommandLine -and $_.CommandLine.ToLower().Contains($pyw) } |
    ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
Start-Sleep -Milliseconds 500

$sh = New-Object -ComObject WScript.Shell
foreach ($lnk in @((Join-Path ([Environment]::GetFolderPath("Programs")) "Gajo.lnk"),
                   (Join-Path ([Environment]::GetFolderPath("Startup")) "Gajo.lnk"))) {
    if ((Test-Path $lnk) -and $sh.CreateShortcut($lnk).Arguments.ToLower().Contains($pyw)) {
        Remove-Item $lnk -Force
        Write-Host "   Quitado: $lnk"
    }
}
if (Test-Path $Destino) { Remove-Item $Destino -Recurse -Force; Write-Host "   Borrada la carpeta $Destino" }

$datos = Join-Path $env:LOCALAPPDATA "Gajo"
if ((Test-Path $datos) -and -not $Silencioso) {
    $r = Read-Host "   ¿Borrar también tu configuración y registro de Gajo? (S/N)"
    if ($r -match '^[sSyY]') { Remove-Item $datos -Recurse -Force; Write-Host "   Borrada la configuración" }
}
Write-Host ""
Write-Host "  Gajo quedó desinstalado." -ForegroundColor Green
