@echo off
chcp 65001 >nul
setlocal enabledelayedexpansion
title YBCO-TA 3.0 一键环境配置

rem ========== 0. 定位脚本所在目录（兼容任意启动位置） ==========
cd /d "%~dp0"
set "SSHDIR=%USERPROFILE%\.ssh"

rem ========== 1. 检测 Git（缺失则用 winget 自动安装） ==========
where git >nul 2>&1
if errorlevel 1 goto :install_git
echo [1/7] [OK] Git 已就绪

rem ========== 2. 检测 Python（缺失或仅 Microsoft Store 占位程序则安装） ==========
set "PYOK=0"
where python >nul 2>&1
if not errorlevel 1 (
    python --version >nul 2>&1
    if not errorlevel 1 set "PYOK=1"
)
if not "!PYOK!"=="1" goto :install_python
for /f "tokens=*" %%v in ('python --version 2^>nul') do set "PYVER=%%v"
echo [2/7] [OK] Python !PYVER!

rem ========== 3. SSH 密钥：生成、备份 config、打印公钥、验证连接 ==========
if not exist "%SSHDIR%" mkdir "%SSHDIR%"
if not exist "%SSHDIR%\id_ed25519" (
    echo [3/7] 生成 SSH 密钥...
    ssh-keygen -t ed25519 -C "teskiel7@gmail.com" -f "%SSHDIR%\id_ed25519" -N ""
    if errorlevel 1 (
        echo [X] 密钥生成失败
        pause
        exit /b 1
    )
) else (
    echo [3/7] [OK] 已存在 SSH 密钥，跳过生成
)

if not exist "%SSHDIR%\config" (
    (echo Host github.com
    echo     Hostname ssh.github.com
    echo     Port 443
    echo     User git) > "%SSHDIR%\config"
    echo [3/7] 已写入 ssh config（github.com 走 ssh.github.com:443）
) else (
    findstr /i /c:"Host github.com" "%SSHDIR%\config" >nul
    if errorlevel 1 (
        copy /y "%SSHDIR%\config" "%SSHDIR%\config.bak" >nul
        (echo.
        echo Host github.com
        echo     Hostname ssh.github.com
        echo     Port 443
        echo     User git) >> "%SSHDIR%\config"
        echo [3/7] 已备份原 config 为 config.bak，并追加 github.com 条目
    ) else (
        echo [3/7] [OK] ssh config 已含 github.com 条目，跳过
    )
)

echo.
echo 请把下面的公钥完整复制，添加到 GitHub：
echo     网页 - Settings - SSH and GPG keys - New SSH key
echo     （标题随意，如 YBCO-TA-3.0-NewPC；类型选 Authentication Key）
echo.
type "%SSHDIR%\id_ed25519.pub"
echo.
pause

:ssh_retry
echo [3/7] 验证 GitHub SSH 连接（ssh.github.com:443）...
ssh -T -o StrictHostKeyChecking=accept-new -o ConnectTimeout=15 git@github.com 2>&1 | findstr /i "successfully authenticated" >nul
if errorlevel 1 (
    echo [X] 验证失败：公钥未生效或网络不通。
    set /p RETRY=公钥已添加后按 Y 重试，按其他键退出:
    if /i "!RETRY!"=="Y" goto :ssh_retry
    pause
    exit /b 1
)
echo [3/7] [OK] SSH 验证通过

rem ========== 4. git 全局 URL 重写：https 改写为 ssh（走 443） ==========
git config --global --get url."git@github.com:".insteadOf >nul 2>&1
if errorlevel 1 (
    git config --global url."git@github.com:".insteadOf "https://github.com/"
    echo [4/7] [OK] 已设置 git 全局 URL 重写：https://github.com/ 改写为 SSH（443 端口）
) else (
    echo [4/7] [OK] URL 重写已存在，跳过
)

rem ========== 5. 获取项目代码：三选一（zip 解压目录 / 已有目录 pull / 全新 clone） ==========
if exist "%~dp0install.py" (
    set "APP_DIR=%~dp0"
    echo [5/7] [OK] 当前目录即 YBCO-TA-3.0 仓库（zip 解压或 clone 所得），直接使用
) else (
    if exist "%~dp0YBCO-TA-3.0\install.py" (
        set "APP_DIR=%~dp0YBCO-TA-3.0\"
        echo [5/7] 检测到已有 YBCO-TA-3.0 目录，执行 git pull 更新...
        git -C "!APP_DIR!" pull --ff-only
        if errorlevel 1 (
            echo [X] git pull 失败，请检查网络后重试
            pause
            exit /b 1
        )
    ) else (
        echo [5/7] 未找到项目代码，开始从 GitHub 克隆（需要 SSH 密钥已生效）...
        git clone git@github.com:Teskiel/YBCO-TA-3.0.git "%~dp0YBCO-TA-3.0"
        if errorlevel 1 (
            echo [X] 克隆失败：请确认网络、SSH 密钥已添加且验证通过
            pause
            exit /b 1
        )
        set "APP_DIR=%~dp0YBCO-TA-3.0\"
        echo [5/7] [OK] 克隆完成
    )
)

rem ========== 6. pip 镜像源（仅首次设置，不覆盖已有配置） ==========
python -m pip config get global.index-url >nul 2>&1
if errorlevel 1 (
    python -m pip config set global.index-url https://pypi.tuna.tsinghua.edu.cn/simple
    echo [6/7] [OK] 已设置 pip 镜像源：清华 TUNA
) else (
    echo [6/7] [OK] pip 镜像源已存在，跳过
)

rem ========== 7. 安装依赖 + 自检 ==========
echo [7/7] 升级 pip / setuptools / wheel...
python -m pip install --upgrade pip setuptools wheel
echo [7/7] 安装项目依赖（约几分钟）...
python -m pip install -r "!APP_DIR!requirements.txt"
if errorlevel 1 (
    echo [X] 依赖安装失败。若为网络原因，可把镜像切回官方源：
    echo       python -m pip config set global.index-url https://pypi.org/simple
    pause
    exit /b 1
)
echo [7/7] 运行安装自检...
python "!APP_DIR!install.py"
if errorlevel 1 (
    echo [X] 自检未通过，请按上方 [X] 项逐条处理
    pause
    exit /b 1
)

echo.
echo [OK] YBCO-TA 3.0 环境配置全部完成！
echo      启动：python Auto_Sweep/app.py （测量 GUI）
echo      硬件：NI-VISA / NI-DAQmx 运行时需另行安装
pause
exit /b 0

rem ========== 子流程：安装 Git ==========
:install_git
echo [1/7] [X] 未检测到 Git
where winget >nul 2>&1
if errorlevel 1 (
    echo 未检测到 winget。请手动安装 Git：下载 https://git-scm.com/download/win 并安装，然后重跑本脚本。
    pause
    exit /b 1
)
echo 尝试用 winget 自动安装 Git（需联网，约 1 分钟）...
winget install --id Git.Git -e --source winget --accept-package-agreements --accept-source-agreements --silent
if errorlevel 1 (
    echo [X] winget 安装 Git 失败，请手动下载安装 https://git-scm.com/download/win，然后重跑本脚本。
    pause
    exit /b 1
)
echo [OK] Git 已安装。PATH 已更新，请关闭本窗口并重新运行 setup.bat 继续。
pause
exit /b 1

rem ========== 子流程：安装 Python ==========
:install_python
echo [2/7] [X] 未检测到可用的 Python
where winget >nul 2>&1
if errorlevel 1 (
    echo 未检测到 winget。请手动安装 Python：python.org 下载 3.12.x，勾选 Add Python to PATH，然后重跑本脚本。
    pause
    exit /b 1
)
echo 尝试用 winget 自动安装 Python 3.12（需联网，约 1-2 分钟）...
winget install --id Python.Python.3.12 -e --source winget --accept-package-agreements --accept-source-agreements --silent
if errorlevel 1 (
    echo [X] winget 安装 Python 失败，请手动安装：python.org 下载 3.12.x，勾选 Add to PATH，然后重跑本脚本。
    pause
    exit /b 1
)
echo [OK] Python 已安装。PATH 已更新，请关闭本窗口并重新运行 setup.bat 继续。
pause
exit /b 1
