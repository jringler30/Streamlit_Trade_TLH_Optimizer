# Contributing

Thank you for your interest in this project. Contributions are welcome.

## Getting Started

```bash
git clone https://github.com/jringler30/portfolio-tlh-optimizer.git
cd portfolio-tlh-optimizer
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

## Guidelines

- **Do not modify core financial logic** (`optimizer_msba_v1_engine.py`) without clearly documenting the change and its impact on simulation output.
- **Keep the UI decoupled from the engine** — simulation logic lives in `optimizer_msba_v1_engine.py`, not in `portfolio_returns_engine.py`.
- **Update `requirements.txt`** if you add a new dependency.
- **Update the README** if you add a new feature, page, or structural change.

## Running the App Locally

```bash
streamlit run portfolio_returns_engine.py
```

## Linting

The project uses `flake8` for style checks (config in `.flake8`):

```bash
flake8 .
```

CI runs automatically on all pull requests to `main`.

## Pull Request Process

1. Fork the repository and create a branch from `main`.
2. Make your changes with clear commit messages.
3. Ensure `flake8 .` passes locally.
4. Open a pull request and fill out the PR template.
