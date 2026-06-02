@echo off
setlocal EnableDelayedExpansion

:: ════════════════════════════════════════════════════════════════
::  Excel 快捷查询助手  —— PyInstaller 打包脚本 (onedir 模式)
::  用法：双击运行，或在项目根目录执行 build.bat
::  产物：dist\ExcelHelper\ExcelHelper.exe
::
::  调试模式：build.bat debug
::    生成带控制台窗口的 exe，可直接看到 Python 日志和 rthook 输出
:: ════════════════════════════════════════════════════════════════

set "PROJECT_DIR=%~dp0"
set "VENV_DIR=%PROJECT_DIR%.venv"
set "SPEC_FILE=%PROJECT_DIR%ExcelHelper.spec"
set "DIST_DIR=%PROJECT_DIR%dist"
set "BUILD_DIR=%PROJECT_DIR%build"
set "OUTPUT_DIR=%DIST_DIR%\ExcelHelper"

:: 是否调试模式（build.bat debug）
set "DEBUG_MODE=0"
if /i "%1"=="debug" set "DEBUG_MODE=1"

echo.
echo ════════════════════════════════════════════════════
echo   Excel 快捷查询助手  PyInstaller 打包工具
echo   模式：onedir（目录发布）
if "%DEBUG_MODE%"=="1" echo   [调试模式：带控制台窗口]
echo ════════════════════════════════════════════════════
echo.

:: ── Step 1: 检查虚拟环境 ─────────────────────────────────────
echo [1/5] 检查虚拟环境...
if not exist "%VENV_DIR%\Scripts\python.exe" (
    echo [错误] 未找到虚拟环境: %VENV_DIR%
    echo        请先运行:  python -m venv .venv
    echo        然后运行:  .venv\Scripts\pip install -r requirements.txt
    pause
    exit /b 1
)
echo       虚拟环境: %VENV_DIR%  OK

:: ── Step 2: 激活虚拟环境 ─────────────────────────────────────
echo [2/5] 激活虚拟环境...
call "%VENV_DIR%\Scripts\activate.bat"
if errorlevel 1 (
    echo [错误] 激活虚拟环境失败
    pause
    exit /b 1
)

:: ── Step 3: 检查 PyInstaller ──────────────────────────────────
echo [3/5] 检查 PyInstaller...
python -c "import PyInstaller" 2>nul
if errorlevel 1 (
    echo       未检测到 PyInstaller，正在安装...
    pip install pyinstaller --quiet
    if errorlevel 1 (
        echo [错误] PyInstaller 安装失败
        pause
        exit /b 1
    )
    echo       PyInstaller 已安装  OK
) else (
    for /f "delims=" %%v in ('python -c "import PyInstaller; print(PyInstaller.__version__)"') do set "PI_VER=%%v"
    echo       PyInstaller !PI_VER!  OK
)

:: ── Step 4: 清理旧产物 ────────────────────────────────────────
echo [4/5] 清理旧构建产物...
if exist "%OUTPUT_DIR%" (
    rmdir /s /q "%OUTPUT_DIR%"
    echo       已删除旧输出目录  OK
)
if exist "%BUILD_DIR%" (
    rmdir /s /q "%BUILD_DIR%"
    echo       已清理 build\  OK
)

:: ── Step 5: 执行打包 ──────────────────────────────────────────
echo [5/5] 开始打包...
echo.
echo       spec:  %SPEC_FILE%
echo       产物:  %OUTPUT_DIR%\ExcelHelper.exe
echo.

if "%DEBUG_MODE%"=="1" (
    :: 调试模式：强制启用控制台窗口
    python -m PyInstaller "%SPEC_FILE%" ^
        --noconfirm ^
        --distpath "%DIST_DIR%" ^
        --log-level WARN ^
        --windowed=0
) else (
    python -m PyInstaller "%SPEC_FILE%" ^
        --noconfirm ^
        --distpath "%DIST_DIR%" ^
        --log-level WARN
)

if errorlevel 1 (
    echo.
    echo ════════════════════════════════════════════════════
    echo   [失败] 打包出错，请查看上方日志
    echo ════════════════════════════════════════════════════
    pause
    exit /b 1
)

:: ── 完成 ──────────────────────────────────────────────────────
echo.
echo ════════════════════════════════════════════════════
echo   [成功] 打包完成！
echo.
echo   发布目录:  %OUTPUT_DIR%\
echo   主程序:    %OUTPUT_DIR%\ExcelHelper.exe
echo.
echo   部署说明:
echo     1. 将 config.yaml 复制到 %OUTPUT_DIR%\ 中
echo        （与 ExcelHelper.exe 同级，程序优先读取此处的配置）
echo     2. onedir 模式启动速度比 onefile 快，无需每次解压
echo     3. 若提示「查询模块未注册」，请用调试模式重新打包：
echo           build.bat debug
echo        然后从命令行运行 exe，查看控制台输出
echo ════════════════════════════════════════════════════
echo.

explorer "%OUTPUT_DIR%"
pause
exit /b 0
