# Contributing to CAVEAT

Thank you for your interest in contributing to CAVEAT.

## Contributor License Agreement

Most contributions require you to agree to a Contributor License Agreement
(CLA) declaring that you have the right to, and actually do, grant us the
rights to use your contribution. For details, visit
[https://cla.opensource.microsoft.com](https://cla.opensource.microsoft.com).

When you submit a pull request, a CLA bot will automatically determine whether
you need to provide a CLA and decorate the pull request appropriately. Follow
the instructions provided by the bot. You only need to do this once across all
repositories using the Microsoft CLA.

## How to contribute

1. Create an issue describing the bug or proposed change when the work is
   substantial or changes benchmark behavior.
2. Fork the repository and create a focused branch.
3. Install the development environment:

   ```bash
   python -m venv .venv
   source .venv/bin/activate
   pip install -e ".[browser,dev]"
   python -m playwright install chromium
   ```

4. Make focused changes and include tests or benchmark validation where applicable.
5. Run the relevant checks:

   ```bash
   ruff check .
   caveat validate
   ```

6. Open a pull request that explains the change and any effect on benchmark
   results, compatibility, or generated artifacts.

Do not include secrets, credentials, private datasets, proprietary code, or
third-party material without a compatible license and required attribution.
Generated frontend artifacts should be updated together with their source and
must preserve upstream license notices.

## Code of Conduct

This project has adopted the
[Microsoft Open Source Code of Conduct](https://opensource.microsoft.com/codeofconduct/).
For more information, see the
[Code of Conduct FAQ](https://opensource.microsoft.com/codeofconduct/faq/) or
contact [opencode@microsoft.com](mailto:opencode@microsoft.com) with questions
or comments.
