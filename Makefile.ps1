<#
.SYNOPSIS
  Windows PowerShell equivalent of Makefile for SelfHeal
.DESCRIPTION
  Usage: .\Makefile.ps1 [target]
  Targets: all, lint, type, test, cov
#>
param([string]$Target = "all")

switch ($Target) {
    "all"  { python -m ruff check src/; if ($LASTEXITCODE -eq 0) { python -m mypy src/ }; if ($LASTEXITCODE -eq 0) { python -m pytest tests/ --tb=short --cov=src/selfheal --cov-report=term-missing --cov-branch -q } }
    "lint" { python -m ruff check src/ }
    "type" { python -m mypy src/ }
    "test" { python -m pytest tests/ -x --tb=short }
    "cov"  { python -m pytest tests/ --tb=short --cov=src/selfheal --cov-report=term-missing --cov-branch }
    default { Write-Host "Targets: all, lint, type, test, cov" }
}
