$ErrorActionPreference = "Stop"

try {
    $scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
    $installDir = "$env:LOCALAPPDATA\Programs\AsinSeeker"
    $sourceExe = Join-Path $scriptDir "AsinSeeker.exe"

    if (-not (Test-Path $sourceExe)) {
        throw "AsinSeeker.exe bulunamadi ($sourceExe). Kurulum dosyalarini (zip) eksiksiz cikardigindan emin ol ve Kurulum.ps1'i AYNI klasorden tekrar calistir."
    }

    New-Item -ItemType Directory -Force -Path $installDir | Out-Null
    Copy-Item -Path $sourceExe -Destination $installDir -Force
    $exePath = Join-Path $installDir "AsinSeeker.exe"

    $WshShell = New-Object -ComObject WScript.Shell

    $startMenuDir = "$env:APPDATA\Microsoft\Windows\Start Menu\Programs"
    $startShortcut = $WshShell.CreateShortcut("$startMenuDir\AsinSeeker.lnk")
    $startShortcut.TargetPath = $exePath
    $startShortcut.WorkingDirectory = $installDir
    $startShortcut.Save()

    $desktopShortcut = $WshShell.CreateShortcut("$env:USERPROFILE\Desktop\AsinSeeker.lnk")
    $desktopShortcut.TargetPath = $exePath
    $desktopShortcut.WorkingDirectory = $installDir
    $desktopShortcut.Save()

    Write-Host ""
    Write-Host "Kurulum tamamlandi!" -ForegroundColor Green
    Write-Host "Program konumu : $exePath"
    Write-Host "Masaustunde ve Baslat menusunde 'AsinSeeker' kisayolu olusturuldu."
    Write-Host ""

    $chromePaths = @(
        "$env:ProgramFiles\Google\Chrome\Application\chrome.exe",
        "${env:ProgramFiles(x86)}\Google\Chrome\Application\chrome.exe"
    )
    $chromeFound = $false
    foreach ($path in $chromePaths) {
        if (Test-Path $path) { $chromeFound = $true }
    }
    if (-not $chromeFound) {
        Write-Host "UYARI: Bu bilgisayarda Google Chrome bulunamadi. AsinSeeker calismak icin Chrome gerektirir -- lutfen https://www.google.com/chrome adresinden kur." -ForegroundColor Red
        Write-Host ""
    }

    Write-Host "ONEMLI: Bu program calismak icin bilgisayarinda Google Chrome'un kurulu olmasini ve internet baglantisi olmasini gerektirir." -ForegroundColor Yellow
    Write-Host "Ilk calistirmada gerekli bazi kucuk bilesenler otomatik kontrol edilir." -ForegroundColor Yellow
    Write-Host ""
    try { Read-Host "Kapatmak icin Enter'a bas" | Out-Null } catch {}
}
catch {
    Write-Host ""
    Write-Host "KURULUM BASARISIZ OLDU:" -ForegroundColor Red
    Write-Host $_.Exception.Message -ForegroundColor Red
    Write-Host ""
    Write-Host "Bu ekrani kapatmadan once, hata mesajini not al ve destek/gelistirici ile paylas." -ForegroundColor Yellow
    Write-Host ""
    try { Read-Host "Kapatmak icin Enter'a bas" | Out-Null } catch {}
    exit 1
}
