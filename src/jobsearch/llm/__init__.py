"""Calling language models: one interface for every provider.

Scoring and evaluation code use `LLMClient` and the types in `jobsearch.llm.types`.
Only `providers/litellm_provider.py` talks to a model SDK.
"""
