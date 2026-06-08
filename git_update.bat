@echo off
cd /d "%~dp0"

echo ========================================
echo   EUROCREM - Subir cambios a GitHub
echo ========================================
echo.

git add index.html eurocrem_batch_v2.3.py update_photos.py debug_photo.py

set /p MSG="Descripcion del cambio: "

git commit -m "%MSG%"
git push origin main

echo.
echo ✅ Cambios subidos a GitHub
pause
