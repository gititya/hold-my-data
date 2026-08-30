# Contributing

Small, focused fixes are welcome.

## Before opening a pull request

1. Create a branch from `main`.
2. Use fake data only. Never commit personal documents, real identifiers, or live secrets.
3. Install the development environment:

   ```bash
   uv venv --python 3.13
   source .venv/bin/activate
   uv pip install '.[dev]'
   ```

4. Add or update a focused test.
5. Run:

   ```bash
   python -m pytest -q
   hold-my-data check
   ```

6. Explain what changed, why, and how you tested it.

Do not add a dependency, model, or network call without explaining its size, license,
privacy effect, and why the existing stack cannot do the job.

## Accuracy changes

State which entity types changed and report both precision and recall. A change that raises
one by silently lowering the other is not a complete result. Never publish the contents of
a private evaluation file.

## Security reports

Follow [SECURITY.md](SECURITY.md). Do not disclose a vulnerability in a public issue.
