@echo off
REM make.bat - Windows equivalent of Makefile for SelfHeal
if "%1"=="" goto :all
if "%1"=="all" goto :all
if "%1"=="lint" goto :lint
if "%1"=="type" goto :type
if "%1"=="test" goto :test
if "%1"=="cov" goto :cov
goto :help

:all
    python -m ruff check src/ && python -m mypy src/ && python -m pytest tests/ --tb=short --cov=src/selfheal --cov-report=term-missing --cov-branch -q
    goto :eof
:lint
    python -m ruff check src/
    goto :eof
:type
    python -m mypy src/
    goto :eof
:test
    python -m pytest tests/ -x --tb=short
    goto :eof
:cov
    python -m pytest tests/ --tb=short --cov=src/selfheal --cov-report=term-missing --cov-branch
    goto :eof
:help
    echo Targets: all, lint, type, test, cov
