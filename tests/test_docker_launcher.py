"""The run launcher that starts every run from the service's own image.

`modern_data_stack.docker_launcher` overrides a private `DockerRunLauncher`
method, so these tests hold two things: that the override returns the launching
container's image ID whatever name the run's origin carries, and that the stock
launcher still routes both of its image-choosing paths through the method
overridden. A Dagster upgrade that renames it would otherwise silently restore
launch-by-tag.

`dagster-docker` is in the `deploy` group, which `just setup` does not install,
so this module skips on a laptop install. CI syncs `deploy` so that it runs.
"""

from __future__ import annotations

import inspect
from types import SimpleNamespace

import pytest

dagster_docker = pytest.importorskip("dagster_docker")
NotFound = pytest.importorskip("docker.errors").NotFound
docker_launcher = pytest.importorskip("modern_data_stack.docker_launcher")

SERVICE_IMAGE = "sha256:" + "5" * 64


class FakeContainers:
    def __init__(self, known: dict[str, str]):
        self.known = known

    def get(self, name: str):
        if name not in self.known:
            raise NotFound(f"no such container: {name}")
        return SimpleNamespace(attrs={"Image": self.known[name]})


def test_own_image_id_is_the_image_the_named_container_runs():
    client = SimpleNamespace(containers=FakeContainers({"c0ffee": SERVICE_IMAGE}))
    assert docker_launcher.own_image_id(client, "c0ffee") == SERVICE_IMAGE


def test_own_image_id_refuses_rather_than_falling_back():
    """No fallback to a tag: that is the drift the module removes."""
    client = SimpleNamespace(containers=FakeContainers({}))
    with pytest.raises(RuntimeError, match="default hostname"):
        docker_launcher.own_image_id(client, "not-a-container")


def test_the_launcher_ignores_the_name_the_run_origin_carries(monkeypatch):
    """The origin says `mds:local`, as a code location with
    DAGSTER_CURRENT_IMAGE set would; the run still gets the service's ID."""
    client = SimpleNamespace(containers=FakeContainers({"svc": SERVICE_IMAGE}))
    monkeypatch.setattr(docker_launcher.docker, "from_env", lambda: client)
    monkeypatch.setattr(docker_launcher.socket, "gethostname", lambda: "svc")
    launcher = docker_launcher.ServiceImageDockerRunLauncher(image="mds:local", network="mds")
    origin = SimpleNamespace(repository_origin=SimpleNamespace(container_image="mds:local"))
    assert launcher._get_docker_image(origin) == SERVICE_IMAGE


@pytest.mark.parametrize("path", ["launch_run", "resume_run"])
def test_the_stock_launcher_still_asks_the_overridden_method(path):
    """Both ways a run gets a container choose its image through
    `_get_docker_image`. If an upgrade inlines or renames it, the subclass
    overrides nothing and runs quietly go back to launching by tag."""
    source = inspect.getsource(getattr(dagster_docker.DockerRunLauncher, path))
    assert "self._get_docker_image(" in source
