# filepath: src/trading/pipeline/__init__.py
"""
Trading pipeline assembly.

Concrete pipeline implementation for trading domain.

Invariant:
- trading domain owns its own pipeline/context/steps under src/trading/pipeline/.
- This package is orchestration glue only (wiring + session iteration).
"""
