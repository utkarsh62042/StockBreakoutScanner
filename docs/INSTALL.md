# Installation & Dependencies

This guide explains how to set up the breakout scanner with all required dependencies.

## Prerequisites

- **Python:** 3.10 or newer ([download](https://www.python.org/downloads/))
- **pip:** Package manager (comes with Python 3.10+)
- **Git:** (optional, for cloning the repo)

## Installation Methods

### Option 1: Quick Install (Recommended)

```bash
# Clone the repository (if you haven't already)
git clone <repo-url>
cd BreakoutStockAnalyser

# Create virtual environment
python -m venv .venv

# Activate virtual environment
# On Windows:
.venv\Scripts\activate
# On macOS/Linux:
source .venv/bin/activate

# Install all core dependencies
pip install -r requirements.txt
```

### Option 2: Install with Telegram Support

If you want real-time Telegram alerts:

```bash
# Activate virtual environment first (see above)

# Install core + Telegram dependencies
pip install -r requirements.txt -r requirements-telegram.txt
```

### Option 3: Development Setup

If you're contributing to the project:

```bash
# Activate virtual environment first

# Install core + development dependencies
pip install -r requirements.txt -r requirements-dev.txt
```

### Option 4: Using pyproject.toml (Modern Method)

```bash
# Activate virtual environment

# Core dependencies only
pip install -e .

# With Telegram support
pip install -e ".[telegram]"

# With development tools
pip install -e ".[dev]"
```

## Dependency Files Explained

### `requirements.txt`
Core dependencies needed to run the scanner:
- **Data processing:** pandas, numpy, scipy
- **Market data:** yfinance, smartapi-python
- **Configuration:** pyyaml, python-dotenv
- **Output:** rich, openpyxl, requests
- **Authentication:** pyotp
- **Logging:** logzero, websocket-client

**Install with:** `pip install -r requirements.txt`

### `requirements-telegram.txt`
Optional Telegram bot support for real-time alerts on your phone.

**Install with:** `pip install -r requirements.txt -r requirements-telegram.txt`

### `requirements-dev.txt`
Development tools: testing, linting, type checking.

**Install with:** `pip install -r requirements.txt -r requirements-dev.txt`

## Verifying Installation

After installation, verify everything works:

```bash
# Check Python version
python --version
# Should show: Python 3.10.x or newer

# Import key libraries (quick sanity check)
python -c "import pandas, numpy, yfinance; print('✓ Core imports OK')"

# Run a simple test
python -m breakout.jobs.morning_scan --help
# Should show the command help without errors
```

## Virtual Environment Best Practices

Always use a virtual environment to avoid conflicts with system Python:

```bash
# Create virtual environment (one-time)
python -m venv .venv

# Activate it (every session)
# Windows:
.venv\Scripts\activate
# macOS/Linux:
source .venv/bin/activate

# Deactivate when done
deactivate
```

Your prompt should show `(.venv)` when active:
```
(.venv) C:\Users\you\BreakoutStockAnalyser>
```

## Troubleshooting

### "ModuleNotFoundError: No module named 'X'"
You forgot to activate the virtual environment or didn't install dependencies.

```bash
# Activate virtual environment
.venv\Scripts\activate  # Windows
source .venv/bin/activate  # macOS/Linux

# Reinstall dependencies
pip install -r requirements.txt
```

### "pip: command not found"
Python is not in your PATH or you're using the wrong Python version.

```bash
# Check Python is installed
python --version
python -m pip --version

# Try with python -m pip instead
python -m pip install -r requirements.txt
```

### Specific package installation fails
Some packages may need compilation. Install build tools:

**Windows:**
```bash
# Install Microsoft C++ Build Tools
# https://visualstudio.microsoft.com/visual-cpp-build-tools/
```

**macOS:**
```bash
xcode-select --install
```

**Linux (Ubuntu/Debian):**
```bash
sudo apt-get install build-essential python3-dev
```

### yfinance rate limiting
If yfinance requests are slow or timing out:

```bash
# Install yfinance-cache for better caching
pip install yfinance-cache
```

The system will use cached data when available.

## Updating Dependencies

To update all packages to their latest versions:

```bash
# Upgrade pip itself
pip install --upgrade pip

# Upgrade all packages in requirements.txt
pip install --upgrade -r requirements.txt
```

## Pinning Specific Versions

The `requirements.txt` uses flexible version constraints (`>=X.Y`). To lock to exact versions for reproducibility:

```bash
# Generate locked versions
pip freeze > requirements-lock.txt

# Install from locked file
pip install -r requirements-lock.txt
```

This ensures everyone uses identical package versions.

## Platform-Specific Notes

### Windows
- Use `.venv\Scripts\activate` to activate virtual environment
- Python paths use backslashes
- Task Scheduler will run the scanner automatically

### macOS
- Use `source .venv/bin/activate` to activate
- Homebrew: `brew install python@3.12` if Python not installed
- cron runs the scanner automatically

### Linux
- Most distributions have Python pre-installed
- Use `python3` if `python` doesn't work
- crontab handles scheduling

## Next Steps

1. Complete the installation steps above
2. Read [START_HERE.md](START_HERE.md) for setup instructions
3. Follow [CONFIGURATION_GUIDE.md](CONFIGURATION_GUIDE.md) to configure the system
4. Run your first scan with `python -m breakout.jobs.morning_scan`

## Questions?

Refer to:
- **Setup issues:** See [CONFIGURATION_GUIDE.md](CONFIGURATION_GUIDE.md)
- **Library conflicts:** Check if virtual environment is activated
- **Missing packages:** Run `pip install -r requirements.txt` again
