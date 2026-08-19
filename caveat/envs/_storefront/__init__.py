"""Shared, invisible backend engine for the harvested-clone marketplace envs.

Each new env (doordash, ebay, …) vendors its OWN real frontend clone (so each LOOKS
like the actual site) and wires that clone's data layer to this one generic API:
catalog list/detail with clean/steered ordering, cart, checkout -> order, soft
leads, and an auto-logged-in user. Only the *data* differs between envs and between
clean/steered; the clone's layout/components/styling are preserved.

Public surface:
    from caveat.envs._storefront.catalog import Item, Catalog, SiteConfig
    from caveat.envs._storefront.adapter import StorefrontEnvironment
    from caveat.envs._storefront.app import build_main   # per-env uvicorn entry
"""
