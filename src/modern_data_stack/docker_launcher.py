"""A Docker run launcher that starts each run from the launching container's own image.

Dagster's `DockerRunLauncher` takes a run's image from the code location's
`DAGSTER_CURRENT_IMAGE`, falling back to its `image` config, and both are a
*name*. A tag moves on every build, while a running service keeps the image it
was created from, so between a rebuild and a redeploy every run executed code
the service was not running: a changed model could run early from a schedule.

This subclass ignores both and asks the Docker daemon, at launch time, which
image the launching container was created from, returning its ID. Runs are
queued, so only the daemon launches them and the launching container is always
the service. A run and the code that launched it are therefore the same image
by construction, and rebuilding changes nothing until the service is recreated.

It needs the docker socket, which the launcher needs anyway, and the
container's default hostname, which Docker sets to the container ID.
"""

from __future__ import annotations

import socket
from typing import Any

import docker
from dagster_docker import DockerRunLauncher
from docker.errors import NotFound


def own_image_id(client: Any, hostname: str | None = None) -> str:
    """The ID of the image the container named `hostname` was created from.

    `hostname` defaults to this process's own, which is the container ID unless
    something set `hostname:`. Raises rather than falling back to a tag: a
    fallback would restore the drift this module exists to remove.
    """
    name = hostname or socket.gethostname()
    try:
        container = client.containers.get(name)
    except NotFound as e:
        raise RuntimeError(
            f"no container {name!r} on this Docker daemon, so the run image cannot "
            "be the service's own. The launcher must run in a container that keeps "
            "its default hostname (the container ID) and mounts the docker socket."
        ) from e
    return container.attrs["Image"]


class ServiceImageDockerRunLauncher(DockerRunLauncher):
    """`DockerRunLauncher`, with every run's image resolved to the launcher's own.

    Overrides `_get_docker_image`, a private method, because it is the one
    place both `launch_run` and `resume_run` ask for an image.
    `tests/test_docker_launcher.py` fails if a Dagster upgrade renames it or
    stops calling it.
    """

    def _get_docker_image(self, job_code_origin: Any) -> str:
        return own_image_id(docker.from_env())
