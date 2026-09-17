# Instalasi

## Prasyarat

Gunakan Python 3.12, Git, dan PostgreSQL. Docker bersifat opsional. Repository `alos-contracts` diperlukan ketika ToolExecutor memvalidasi payload kanonis.

## Windows PowerShell

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"
Copy-Item .env.example .env
```

## Linux/macOS

```bash
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e '.[dev]'
cp .env.example .env
```

Jangan menggunakan nilai password contoh pada environment bersama atau production.
