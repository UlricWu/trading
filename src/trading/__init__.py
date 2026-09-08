# filepath: src/trading/__init__.py
"""
Trading domain.

This package defines the stable domain boundaries:
- market (facts)
- signal (model output facts)
- portfolio construction (ideal targets, no制度)
- execution (制度 world: T+1/limits/cash/lot, costs, slippage, ledger)
- sim kernel (replay clock + orchestration)
- reporting (read-only report generation from formal artifacts or facts)
"""
