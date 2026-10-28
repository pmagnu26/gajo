# Instalador de Gajo para Windows 10/11.
# Instala lo que falte (Python 3.12, ffmpeg, Pandoc y, si se quiere, LibreOffice), las librerías
# de Python, copia la app a %LOCALAPPDATA%\Programs\Gajo y crea los accesos directos.
param(
    [string]$Destino = (Join-Path $env:LOCALAPPDATA "Programs\Gajo"),
    [switch]$SinDependencias,   # no instala programas externos (para pruebas)
    [switch]$SinAccesos,        # no crea accesos directos (para pruebas)
    [switch]$SinIniciar,        # no abre Gajo al terminar
    [switch]$Silencioso         # responde "sí" a todo
)
$ErrorActionPreference = "Stop"
$app = Join-Path $PSScriptRoot "app"

function Paso($t) { Write-Host ""; Write-Host "== $t" -ForegroundColor Cyan }
function Bien($t) { Write-Host "   $t" -ForegroundColor Green }
function Aviso($t) { Write-Host "   $t" -ForegroundColor Yellow }
function Preguntar($t) {
    if ($Silencioso) { return $true }
    $r = Read-Host "   $t (S/N)"
    return $r -match '^[sSyY]'
}
function Con-Winget($id, $nombre) {
    if (-not (Get-Command winget -ErrorAction SilentlyContinue)) {
        throw "Falta winget ('Instalador de aplicación' de Microsoft Store). Instálalo y vuelve a abrir este instalador."
    }
    Aviso "Instalando $nombre (puede tardar unos minutos)..."
    winget install --id $id -e --silent --accept-source-agreements --accept-package-agreements | Out-Host
}
function Buscar-Python {
    $candidatos = @()
    if (Get-Command py -ErrorAction SilentlyContinue) {
        try {
            $p = & py -3.12 -c "import sys; print(sys.executable)" 2>$null
            if ($LASTEXITCODE -eq 0 -and $p) { $candidatos += $p.Trim() }
        } catch { }
    }
    $candidatos += (Join-Path $env:LOCALAPPDATA "Programs\Python\Python312\python.exe")
    $candidatos += "C:\Program Files\Python312\python.exe"
    foreach ($c in $candidatos) { if ($c -and (Test-Path $c)) { return $c } }
    return $null
}
function Existe-Alguno($rutas) {
    foreach ($r in $rutas) {
        foreach ($hit in (Get-ChildItem -Path $r -ErrorAction SilentlyContinue)) { return $true }
    }
    return $false
}

try {
    Write-Host ""
    Write-Host "  GAJO - conversor de archivos y capturas de pantalla, sin internet" -ForegroundColor White
    Write-Host "  Se instalará en: $Destino"

    if (-not (Test-Path (Join-Path $app "gajo.pyw"))) { throw "No encuentro la carpeta 'app'. Descomprime todo el .zip antes de instalar." }

    Paso "1/5  Python 3.12"
    $python = Buscar-Python
    if (-not $python) {
        if ($SinDependencias) { throw "No hay Python 3.12 en este PC." }
        Con-Winget "Python.Python.3.12" "Python 3.12"
        $python = Buscar-Python
        if (-not $python) { throw "No se pudo instalar Python 3.12." }
    }
    Bien "Python: $python"
    $pythonw = Join-Path (Split-Path $python) "pythonw.exe"

    Paso "2/5  Programas de apoyo (ffmpeg, Pandoc, LibreOffice)"
    $ffmpeg = (Get-Command ffmpeg -ErrorAction SilentlyContinue) -or (Existe-Alguno @(
        "$env:LOCALAPPDATA\Microsoft\WinGet\Links\ffmpeg.exe",
        "$env:LOCALAPPDATA\Microsoft\WinGet\Packages\Gyan.FFmpeg*\ffmpeg-*\bin\ffmpeg.exe"))
    $pandoc = (Get-Command pandoc -ErrorAction SilentlyContinue) -or (Existe-Alguno @(
        "$env:LOCALAPPDATA\Pandoc\pandoc.exe", "C:\Program Files\Pandoc\pandoc.exe"))
    $office = Existe-Alguno @("C:\Program Files\LibreOffice\program\soffice.exe",
                              "C:\Program Files (x86)\LibreOffice\program\soffice.exe")
    if ($ffmpeg) { Bien "ffmpeg ya está (audio y video)" }
    elseif (-not $SinDependencias) { Con-Winget "Gyan.FFmpeg" "ffmpeg (audio y video)" }
    if ($pandoc) { Bien "Pandoc ya está (documentos)" }
    elseif (-not $SinDependencias) { Con-Winget "JohnMacFarlane.Pandoc" "Pandoc (documentos)" }
    if ($office) { Bien "LibreOffice ya está (Word, Excel y PowerPoint)" }
    elseif (-not $SinDependencias) {
        Aviso "LibreOffice (unos 350 MB) solo se usa para convertir Word, Excel y PowerPoint."
        if (Preguntar "¿Instalar LibreOffice?") { Con-Winget "TheDocumentFoundation.LibreOffice" "LibreOffice" }
        else { Aviso "Sin LibreOffice: las conversiones de Office avisarán que falta." }
    }

    Paso "3/5  Librerías de Python (la primera vez descarga unos 300 MB)"
    & $python -m pip install --disable-pip-version-check --upgrade -r (Join-Path $app "requirements.txt")
    if ($LASTEXITCODE -ne 0) {
        Aviso "Reintentando solo para este usuario..."
        & $python -m pip install --user --disable-pip-version-check --upgrade -r (Join-Path $app "requirements.txt")
        if ($LASTEXITCODE -ne 0) { throw "No se pudieron instalar las librerías de Python (¿hay internet?)." }
    }
    Bien "Librerías listas"

    Paso "4/5  Copiando Gajo"
    Get-CimInstance Win32_Process -Filter "Name='pythonw.exe'" -ErrorAction SilentlyContinue |
        Where-Object { $_.CommandLine -and $_.CommandLine.ToLower().Contains((Join-Path $Destino "gajo.pyw").ToLower()) } |
        ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
    Start-Sleep -Milliseconds 500
    New-Item -ItemType Directory -Force -Path $Destino | Out-Null
    robocopy $app $Destino /MIR /XD __pycache__ /NFL /NDL /NJH /NJS /NP | Out-Null
    if ($LASTEXITCODE -ge 8) { throw "No se pudieron copiar los archivos a $Destino" }
    & $python -c "import sys; sys.path.insert(0, r'$Destino'); import gajo.app, gajo.capture, gajo.panels.image_panels" 2>&1 | Out-Host
    if ($LASTEXITCODE -ne 0) { throw "Gajo quedó copiado pero no abre; revisa el mensaje de arriba." }
    Bien "Copiado en $Destino"

    Paso "5/5  Accesos directos"
    if (-not $SinAccesos) {
        $sh = New-Object -ComObject WScript.Shell
        $menu = Join-Path ([Environment]::GetFolderPath("Programs")) "Gajo.lnk"
        $inicio = Join-Path ([Environment]::GetFolderPath("Startup")) "Gajo.lnk"
        $destinos = @($menu)
        if (Preguntar "¿Que Gajo parta solo al prender el PC?") { $destinos += $inicio }
        elseif (Test-Path $inicio) { Remove-Item $inicio -Force }
        foreach ($lnk in $destinos) {
            $l = $sh.CreateShortcut($lnk)
            $l.TargetPath = $pythonw
            $l.Arguments = '"' + (Join-Path $Destino "gajo.pyw") + '"'
            $l.WorkingDirectory = $Destino
            $l.IconLocation = Join-Path $Destino "gajo.ico"
            $l.Description = "Gajo: conversor de archivos y capturas de pantalla"
            $l.Save()
        }
        Bien "Acceso 'Gajo' en el menú Inicio"
        if ($destinos.Count -gt 1) { Bien "Partirá solo al prender el PC" }
    }

    if (Get-Process Lightshot -ErrorAction SilentlyContinue) {
        Aviso "Lightshot está abierto y usa Ctrl+Impr Pant, el mismo atajo de la captura de Gajo."
        if (Preguntar "¿Cerrar Lightshot ahora?") {
            Stop-Process -Name Lightshot -Force
            Aviso "Para que no vuelva a partir: Administrador de tareas > Aplicaciones de arranque > Lightshot > Deshabilitar."
        }
    }

    if (-not $SinIniciar) {
        Start-Process -FilePath $pythonw -ArgumentList ('"' + (Join-Path $Destino "gajo.pyw") + '"') -WorkingDirectory $Destino
    }
    Write-Host ""
    Write-Host "  Listo. Gajo quedó instalado: busca el ícono naranjo junto al reloj." -ForegroundColor Green
    Write-Host "  Arrastra un archivo y, sin soltar, toca Shift, Ctrl o Alt. Ctrl+Impr Pant captura la pantalla."
    exit 0
}
catch {
    Write-Host ""
    Write-Host "  No se pudo terminar la instalación:" -ForegroundColor Red
    Write-Host "  $($_.Exception.Message)" -ForegroundColor Red
    exit 1
}
