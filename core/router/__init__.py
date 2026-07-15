"""The Router — economic brain (§4.4): semantic cache, cross-agent dedup, cheapest-accurate
routing, and a decision log proving savings. See router.py.
"""

from core.router.router import Router, classify_step  # noqa: F401
from core.router.log import RouterDecision, RouterLog  # noqa: F401
