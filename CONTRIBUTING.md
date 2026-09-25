# Contributing to Jev Trades

First off, thank you for considering contributing to Jev Trades! It's people like you that make Jev Trades such a great tool for crypto market data and autonomous trading.

## Where do I go from here?

If you've noticed a bug or have a feature request, make sure to check if there's already an open issue on our [GitHub repository](https://github.com/zadescoxp/Jev-Trades/issues). If not, feel free to open a new one!

## Fork & create a branch

If this is something you think you can fix, then fork Jev Trades and create a branch with a descriptive name.

## Setup your environment

Jev Trades has two main components: a Next.js frontend and a Python backend.

### Prerequisites
- Node.js 20 or newer
- Python 3.12 recommended

### Installation

1. Install Python dependencies:
   ```bash
   python -m venv .venv
   source .venv/bin/activate  # Or .venv\Scripts\activate on Windows
   pip install -r pipeline/requirements.txt
   ```

2. Install Node.js dependencies:
   ```bash
   npm install
   ```

## Development Workflow

When making changes, please ensure you test both the dashboard and the Python feed. 

### Running locally
You will need two terminals to run the full application:

**Terminal 1 (Python Feed):**
```bash
source .venv/bin/activate
python pipeline/data_collector.py
```

**Terminal 2 (Next.js Dashboard):**
```bash
npm run dev
```

### Making Changes
- **Dashboard (Next.js):** Code is located in `app/` and related directories.
- **Backend (Python):** Code is located in `pipeline/`. 

## Validating your changes

Before submitting a pull request, please run the following commands to ensure your changes pass our linters and build checks:

```bash
# Check Next.js linting
npm run lint

# Check Next.js build
npm run build

# Check Python syntax compilation
python3 -m py_compile pipeline/data_collector.py pipeline/paper_trader.py pipeline/schema.py
```
*(Note: Use `python` instead of `python3` if you are on Windows)*

## Submitting a Pull Request

1. Commit your changes with a clear and descriptive commit message.
2. Push your branch to your fork.
3. Open a Pull Request against the `main` branch of the original repository.
4. Provide a detailed description of your changes, including any relevant issue numbers.

## Code of Conduct

Please note that this project is released with a Contributor Code of Conduct. By participating in this project you agree to abide by its terms.

Thank you for contributing!
