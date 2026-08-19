# Audited generator dependency

`harness_distill-0.1.0-py3-none-any.whl` is a frozen legacy dependency built
mechanically from clean commit
`71d1cc3e76c4b7ab001100845d789080a26a0d01`. The campaign loader verifies both
the wheel hash and the hashes of `procedural.py`, `schemas.py`, and `split.py`
before using it. The wheel supplies the deterministic procedural generator and
the already-audited Qwen3.5 full-model smoke and BF16-aware merge verifier; it
does not supply training data.

Its filename and Python import namespace cannot be renamed without rebuilding
the wheel and invalidating the recorded dependency hash. They are retained as
compatibility identities, not as CAVEAT component names.
