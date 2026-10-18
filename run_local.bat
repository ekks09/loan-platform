@echo off
setlocal
REM Local development launcher for Windows.
REM Django serves both the API and the HTML pages, so only one server is needed.

cd /d "%~dp0"

if not exist .venv (
  echo Creating virtual environment...
  python -m venv .venv || goto :error
)

call .venv\Scripts\activate.bat || goto :error

echo Installing dependencies...
python -m pip install --quiet --upgrade pip || goto :error
pip install --quiet -r Backend\requirements.txt || goto :error

if "%DJANGO_SECRET_KEY%"=="" set DJANGO_SECRET_KEY=dev-only-secret-key-that-is-not-used-in-any-deployment-0001
set DJANGO_DEBUG=1
set DJANGO_ALLOW_INSECURE_SECRETS=1
set DJANGO_DEV_SQLITE=1
set DJANGO_SQLITE_PATH=dev.sqlite3
set PAYMENTS_ENABLED=false
set SMS_BACKEND=console

cd Backend
echo Applying migrations...
python manage.py migrate --noinput || goto :error

echo.
echo Starting the platform at http://127.0.0.1:8000
echo Press Ctrl+C to stop.
python manage.py runserver 127.0.0.1:8000
goto :eof

:error
echo.
echo Setup failed. Check the messages above.
exit /b 1
