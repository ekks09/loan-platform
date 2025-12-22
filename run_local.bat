@echo off
ECHO "Setting up environment and starting servers..."

REM Activate virtual environment
CALL env\Scripts\activate.bat

REM Start Django backend server in a new window
ECHO "Starting Django backend server in a new window. Go to http://127.0.0.1:8000"
START "Backend" cmd /k "cd Backend && python manage.py runserver"

REM Start frontend static file server in a new window
ECHO "Starting frontend server in a new window. Go to http://127.0.0.1:8001"
START "Frontend" cmd /k "cd Frontend && python -m http.server 8001"

ECHO "Both servers have been started in separate windows."
