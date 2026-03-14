"""
Storage package for Semabridge.

Provides the SQLAlchemy ORM abstraction layer that defaults to DuckDB
for local/CI usage and optionally targets PostgreSQL for distributed
enterprise clusters.
"""
