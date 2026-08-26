@echo off
rem KID 个人测量 GUI 启动脚本
chcp 65001 >nul
cd /d "%~dp0"
"C:\Users\smlab\AppData\Local\Programs\Python\Python314\python.exe" kid_measurement_gui_personal.py
if errorlevel 1 pause
