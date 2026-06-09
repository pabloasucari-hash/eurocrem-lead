@echo off
cd /d "%~dp0"

echo ========================================
echo   EUROCREM - Deploy a GitHub
echo ========================================

rem Limpiar estado roto de git
if exist ".git\index.lock" del /F ".git\index.lock"
if exist ".git\MERGE_HEAD" del /F ".git\MERGE_HEAD"
if exist ".git\rebase-merge" rd /s /q ".git\rebase-merge"
if exist ".git\rebase-apply" rd /s /q ".git\rebase-apply"
git checkout main 2>nul

rem Copiar archivo
echo Copiando index-efa8f914.html a index.html...
copy /Y "index-efa8f914.html" "index.html"

rem Agregar solo archivos del proyecto
git add index.html deploy.bat
if exist "eurocrem_batch_v2.3.py" git add eurocrem_batch_v2.3.py
if exist "eurocrem_vdrmota_batch_v1.py" git add eurocrem_vdrmota_batch_v1.py
if exist "EUROCREM_PIPELINE_DOCS.md" git add EUROCREM_PIPELINE_DOCS.md
if exist "update_photos.py" git add update_photos.py
if exist "debug_photo.py" git add debug_photo.py

set /p MSG="Descripcion del cambio: "
git commit -m "%MSG%"

rem Integrar remotos y pushear
git fetch origin main
git merge origin/main -X ours --no-edit
git push origin main

echo.
echo Subido a GitHub correctamente
pause
