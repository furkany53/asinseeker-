---
name: rebuild-and-deploy
description: Rebuild AsinSeeker.exe from source, reinstall it via Kurulum.ps1, and refresh the isolated AsinSeeker_TEST copy. Use this after making changes to keepa_gui.py or any module it depends on, whenever the user asks to rebuild, yeniden derle, kısayolu güncelle, or test kopyasını yenile.
disable-model-invocation: true
---

# Rebuild and Deploy AsinSeeker

Bu proje icin build+kurulum+test-kopyasi rutinini calistirir. CLAUDE.md'deki
"Commands" bolumunde tanimli adimlarin tek seferlik uygulamasidir.

## Adimlar

1. Onceki calisan AsinSeeker/show_tab surecleri varsa kapat (stale pencere
   yakalamayi onlemek icin):
   ```powershell
   Get-CimInstance Win32_Process -Filter "Name='python.exe'" | Where-Object { $_.CommandLine -match 'keepa_gui|show_tab' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
   ```

2. Rebuild:
   ```bash
   cd build_tmp && python -m PyInstaller AsinSeeker.spec --distpath ../dist --workpath .
   ```
   "Build complete!" ciktisini dogrula.

3. Kur:
   ```bash
   powershell -ExecutionPolicy Bypass -File dist/Kurulum.ps1
   ```

4. Test kopyasini guncelle (varsa):
   ```bash
   cp dist/AsinSeeker.exe "/c/Users/onury/Desktop/AsinSeeker_TEST/AsinSeeker.exe"
   ```

5. `python test_gui_smoke.py` ile son bir dogrulama yap.

6. Kullaniciya ozet ver: build basarili mi, kurulu kopya guncellendi mi, test
   kopyasi guncellendi mi.

Not: Bu skill sadece kullanici tarafindan cagrilir (side-effect'li -- gercek
kurulu programi degistirir), Claude kendi basina otomatik tetiklememeli.
