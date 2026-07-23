# filepath: src/training/__init__.py
"""
Training domain.

This project explicitly supports TWO distinct training paradigms.

These paradigms are NOT interchangeable optimizations.
They represent fundamentally different assumptions about
data scale, model semantics, and system responsibility.

------------------------------------------------------------
Paradigm A: Day-Scoped Batch Training
------------------------------------------------------------

Definition:
- TrainingUnit = TradingDay
- Model        = Batch (fit on finite dataset)
- Window       = 1–5 trading days (explicit, finite)

Semantics:
- Each training run operates on a CLOSED, FINITE dataset.
- Datasets are fully materialized for the given window.
- Model parameters are stateless across runs unless
  explicitly reloaded by the workflow.

System assumptions:
- Dataset size per training run fits comfortably in memory.
- Training is reproducible and restartable by re-running the job.
- Feature contracts and label semantics are stable.

Intended use:
- Research and experimentation
- Alpha validation
- Small to medium scale datasets
- Interpretable batch models (LR, RF, XGBoost-small)

Non-goals:
- Continuous adaptation
- Unbounded historical aggregation
- Stateful long-running optimization


------------------------------------------------------------
Paradigm B: Event-Stream Online Training
------------------------------------------------------------

Definition:
- TrainingUnit = EventStream
- Model        = Online (partial_fit / incremental update)
- State        = Explicitly checkpointed

Semantics:
- Training operates on an UNBOUNDED data stream.
- Samples are consumed, applied to model state, and discarded.
- The trained model IS the accumulated state.

System assumptions:
- Dataset cannot be fully materialized on a single machine.
- Training is defined as state evolution, not a single job.
- Checkpointing is required for recovery and reproducibility.

Intended use:
- Production-scale learning
- Long historical horizons
- High-frequency or high-cardinality data
- Memory-safe, long-running training processes

Non-goals:
- Exact batch optimality
- One-shot reproducible fits without state
- Small-scale research convenience


------------------------------------------------------------
Doctrine Enforcement
------------------------------------------------------------

The training pipeline MUST choose exactly ONE paradigm
for a given training run.

Mixing paradigms (e.g. batch models with unbounded datasets)
is considered a fatal configuration error.

When dataset size exceeds single-machine batch capacity,
the system MUST switch from Day-Scoped Batch Training
to Event-Stream Online Training.

This is a semantic requirement, not a performance optimization.
"""
