"""Rule/correlation engine — stage 3 of the pipeline."""

from wipertrace.engine.context import EvalContext
from wipertrace.engine.evaluator import evaluate
from wipertrace.engine.signatures import SignatureDB, SignatureError, load_signatures

__all__ = ["EvalContext", "SignatureDB", "SignatureError", "evaluate", "load_signatures"]
