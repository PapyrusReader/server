# Papyrus server

REST API server for the Papyrus project.

## Requirements

- Python 3.12+.
- PostgreSQL 15+.

## Installation

Install dependencies:

```bash
pip install -e ".[dev]"
```

Run the server:

```bash
uvicorn papyrus.main:app --reload
```

## Development

```bash
pytest --cov
```

Formatting and linting:

```bash
ruff format .
ruff check .
```
