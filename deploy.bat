@echo off
cd /d "%~dp0"

echo ========================================
echo   EUROCREM - Deploy a GitHub
echo ========================================

git add index.html eurocrem_batch_v2.3.py update_photos.py debug_photo.py deploy.bat

set /p MSG="Descripcion del cambio: "
git commit -m "%MSG%"

git push origin main

echo.
echo ✅ Subido a GitHub correctamente
pause
