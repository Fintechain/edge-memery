# Contributing to Memery

Thank you for helping improve Memery. Contributions should preserve its local-first design, explicit uncertainty, stable top-level context, and measurable performance.

## Development Setup

Clone the repository and run the following commands from its root directory:

```bash
python -m venv .venv
```

Activate the environment, then install the project with development tools:

```bash
python -m pip install --upgrade pip
pip install -e ".[dev]"
```

Install optional integrations when working on those features:

```bash
pip install -e ".[all]"
```

## Before Opening a Pull Request

1. Keep changes focused and avoid committing runtime databases or vector data.
2. Add or update tests for behavioral changes.
3. Run the test suite:

   ```bash
   python -m pytest
   ```

4. Run an appropriate benchmark when changing storage, retrieval, concurrency, or summary generation:

   ```bash
   python -m memory_server.benchmark_stress --memories 20000 --operations 20000 --workers 32
   ```

5. Update `README.md`, `CHANGELOG.md`, or both when changing public behavior.

## Pull Requests

- Explain the problem and the chosen solution.
- Include test and benchmark results when relevant.
- Call out schema, migration, compatibility, privacy, or dependency implications.
- Do not mix unrelated refactors into a functional change.
- By submitting a contribution, you agree that it is licensed under Apache License 2.0 as described in Section 5 of the license.

## Reporting Bugs and Requesting Features

Use the GitHub issue templates. Include the operating system, Python version, dependency versions, reproduction steps, expected behavior, and actual behavior. Remove secrets and personal memory content before sharing logs or databases.

## Security Issues

Do not report vulnerabilities in a public issue. Follow [SECURITY.md](SECURITY.md).
