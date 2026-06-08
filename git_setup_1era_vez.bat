@echo off
cd /d "%~dp0"

echo ========================================
echo   EUROCREM - Setup inicial GitHub
echo   (Correr UNA SOLA VEZ)
echo ========================================
echo.

git init
git remote add origin https://github.com/pabloasucari-hash/eurocrem-lead.git
git branch -M main

git add index.html eurocrem_batch_v2.3.py update_photos.py debug_photo.py deploy.bat git_setup_1era_vez.bat git_update.bat

git commit -m "v2.3 - setup inicial: mapa, fotos, UI compacta"

git push -u origin main

echo.
echo ✅ Repo configurado y subido a GitHub
echo.
echo Proximas veces usa git_update.bat
pause
