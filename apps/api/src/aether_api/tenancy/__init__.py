"""Tenant identity dependencies — :func:`current_user`, :func:`csrf_dependency`,
:func:`admin_required`.

Defense-in-depth note: the tenant filter is enforced at TWO layers —
this module (extracts ``current_user`` from the verified session) AND
the repository layer (every query goes through ``_for_user``). Removing
either layer is a security regression.
"""
