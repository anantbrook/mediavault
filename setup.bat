@echo off
SETLOCAL

:: Check for Python
python --version >nul 2>&1
IF %ERRORLEVEL% NEQ 0 (
    echo Python is not installed. Please install Python and try again.
    exit /b
)

:: Create virtual environment
echo Creating virtual environment...
python -m venv venv

:: Activate the virtual environment
call venv\Scripts\activate

:: Install dependencies
echo Installing dependencies...
pip install -r requirements.txt

:: Initialize database (assuming a script named init_db.py exists)
echo Initializing database...
python init_db.py

:: Run tests (assuming a script named run_tests.py exists)
echo Running tests...
python run_tests.py

:: Start Flask application
echo Starting Flask application on http://localhost:5050...
set FLASK_APP=app.py
flask run --host=0.0.0.0 --port=5050

ENDLOCAL
pause