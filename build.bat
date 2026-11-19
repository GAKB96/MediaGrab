@echo off
REM Local build (alternative to GitHub Actions). Needs Python 3.10+ installed.
REM After building, put ffmpeg.exe, ffprobe.exe and deno.exe next to dist\MediaGrab.exe.
python -m pip install -U "yt-dlp[default]" pyinstaller || goto :err
pyinstaller --noconfirm --onefile --windowed --name MediaGrab --icon assets\mediagrab.ico --collect-all yt_dlp_ejs app\mediagrab.py || goto :err
xcopy /E /I /Y opera-extension dist\opera-extension >nul
echo.
echo Done: dist\MediaGrab.exe
echo Now copy ffmpeg.exe, ffprobe.exe and deno.exe into the dist folder.
pause
exit /b 0
:err
echo Build failed.
pause
exit /b 1
