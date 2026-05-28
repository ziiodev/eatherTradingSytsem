"""Per-project Docker orchestration control plane.

The API process NEVER touches ``/var/run/docker.sock`` directly. Every
Docker API call inside this package speaks HTTP to the
``tecnativa/docker-socket-proxy`` sidecar bound to
``settings.docker_host`` (default ``tcp://docker-proxy:2375``). The
proxy's allowlist grants only CONTAINERS / IMAGES / BUILD; EXEC,
NETWORKS, VOLUMES, SWARM, INFO, AUTH, SECRETS, SERVICES, TASKS, NODES,
SYSTEM are denied — a compromised API process therefore cannot pivot to
host-root via the socket.

Module map:

* :mod:`.client`             — :class:`aiodocker.Docker` singleton.
* :mod:`.sanitize`           — strict allowlist for any value that lands
                              in a Dockerfile / container name / label.
* :mod:`.dockerfile`         — pure Jinja2 renderer (no Docker calls).
* :mod:`.lifecycle`          — build / create / start / pause / stop /
                              recreate / remove driving status
                              transitions via
                              :mod:`aether_api.services.project_lifecycle`.
* :mod:`.events_repository`  — writer for ``container_events`` audit rows.
* :mod:`.reconcile`          — boot + periodic drift sweep.
"""

from __future__ import annotations
