@echo off
rem KID 个人测量 GUI 启动脚本
rem
rem 机器差异（Python 解释器绝对路径）不要在入库脚本里写死：
rem 历史上这里写的是 C:\Users\smlab\...\Python314\python.exe，换台电脑就坏。
rem 现在按 YBCO_PYTHON → python → py -3 的顺序探测。
chcp 65001 >nul
cd /d "%~dp0"

set "PYEXE="
if defined YBCO_PYTHON if exist "%YBCO_PYTHON%" set "PYEXE=%YBCO_PYTHON%"

if not defined PYEXE (
    where python >nul 2>&1 && set "PYEXE=python"
)
if not defined PYEXE (
    where py >nul 2>&1 && set "PYEXE=py -3"
)
if not defined PYEXE (
    echo [X] 找不到 Python。请安装 Python 3，或设置环境变量 YBCO_PYTHON 指向 python.exe
    pause
    exit /b 1
)

%PYEXE% kid_measurement_gui_personal.py
if errorlevel 1 pause
