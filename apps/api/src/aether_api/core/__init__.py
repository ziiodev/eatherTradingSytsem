"""Core cross-cutting concerns: settings, logging, primitives.

Modules here are NOT allowed to import from :mod:`aether_api.auth`,
:mod:`aether_api.db`, :mod:`aether_api.routers`, or any other "feature"
package. They sit at the bottom of the dependency graph so anything else
can pull in :func:`get_settings` and :func:`setup_logging` without risking
import cycles.
"""
