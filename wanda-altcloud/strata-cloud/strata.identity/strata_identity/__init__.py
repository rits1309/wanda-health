"""strata.identity — the identity-logic library for Savanna.

The token-verification seam, the identity wire types, and the profile/role
helpers that implement the Cognito sync rule. The identity **models and
migrations** live in ``strata-core`` (the canonical
database project); this package imports them like every other
consumer. Consumed by ``strata.engine.auth`` (and later services) as a local
path dependency.
"""
