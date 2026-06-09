@echo off
cd /d "%~dp0"

echo ========================================
echo   EUROCREM - Deploy a GitHub
echo ========================================

rem Eliminar lock si existe
if exist ".git\index.lock" del /F ".git\index.lock"
if exist ".git\MERGE_HEAD" del /F ".git\MERGE_HEAD"

rem Copiar archivo primero
echo Copiando index-efa8f914.html a index.html...
copy /Y "index-efa8f914.html" "index.html"

rem Agregar y commitear cambios locales
git add index.html eurocrem_batch_v2.3.py update_photos.py debug_photo.py deploy.bat eurocrem_vdrmota_batch_v1.py EUROCREM_PIPELINE_DOCS.md

set /p MSG="Descripcion del cambio: "
git commit -m "%MSG%"

rem Ahora traer cambios remotos y pushear
git pull origin main --rebase --autostash
git push origin main

echo.
echo ✅ Subido a GitHub correctamente
pause
