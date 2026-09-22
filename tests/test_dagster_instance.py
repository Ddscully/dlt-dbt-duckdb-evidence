"""The two Dagster instance configs, and what holds them together.

`.dagster/dagster.yaml` is the laptop's instance and `deploy/dagster.yaml` the
deployed one. They differ in exactly one thing — where run, event and schedule
storage lives — and agree in everything else, because every behaviour measured
on a laptop is only evidence about the deployment while they do.

Dagster has no include directive, so that agreement is two files a person keeps
in step by hand, which is what these tests are for. The failure they exist to
catch is silent: an instance whose `max_concurrent_runs` quietly went back to
Dagster's default of ten would launch ten DuckDB writers and fail somewhere
downstream of the cause, exactly as a missing `LAKEHOUSE_DIR` does.

The second shape here is an environment variable: Dagster resolves `{env: X}`
when the instance loads, and a name nothing assigns is a `DagsterInvalidConfigError`
at *startup*, naming the variable. That one is loud. It is guarded anyway
because the fix is to set it somewhere, and `.env.example` is where this repo
says what there is to set.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

yaml = pytest.importorskip("yaml")

REPO_ROOT = Path(__file__).resolve().parent.parent
LAPTOP_INSTANCE = REPO_ROOT / ".dagster/dagster.yaml"
DEPLOYED_INSTANCE = REPO_ROOT / "deploy/dagster.yaml"
COMPOSE = REPO_ROOT / "compose.yaml"
DOCKERFILE = REPO_ROOT / "Dockerfile"
DEVCONTAINER_DOCKERFILE = REPO_ROOT / ".devcontainer/Dockerfile"
DEPENDABOT = REPO_ROOT / ".github/dependabot.yml"
PYTHON_VERSION = REPO_ROOT / ".python-version"
PAGES_WORKFLOW = REPO_ROOT / ".github/workflows/pages.yml"

# The compose service the deployed instance runs as, and whose environment is
# what the run launcher copies from.
DAGSTER_SERVICE = "dagster"

# What the two instances must say the same way. Not "everything but storage":
# naming them makes adding a block a decision about both files.
SHARED_BLOCKS = ("telemetry", "concurrency", "retention")

# The storage blocks that only the deployed instance has.
DEPLOYED_ONLY = ("storage", "local_artifact_storage", "compute_logs")


def load(path: Path) -> dict:
    return yaml.safe_load(path.read_text())


def env_names(node: object) -> set[str]:
    """Every name in an `{env: NAME}` mapping anywhere under `node`."""
    if isinstance(node, dict):
        if set(node) == {"env"}:
            return {node["env"]}
        return set().union(*(env_names(v) for v in node.values()), set())
    if isinstance(node, list):
        return set().union(*(env_names(v) for v in node), set())
    return set()


def leaves(node: object, path: tuple[str, ...] = ()) -> list[tuple[tuple[str, ...], object]]:
    """Every scalar under `node`, with the key path that reaches it.

    An `{env: NAME}` mapping counts as one leaf, not as the string NAME: it is
    the thing being asserted about, so it must not be flattened past.
    """
    if isinstance(node, dict) and set(node) != {"env"}:
        return [pair for k, v in node.items() for pair in leaves(v, (*path, str(k)))]
    return [(path, node)]


def test_both_instance_files_parse_and_are_not_empty():
    """The vacuity guard: every test below reads keys out of these, and
    `yaml.safe_load` of an empty or commented-out file is None, which would make
    the agreement tests pass by having nothing to compare."""
    for path in (LAPTOP_INSTANCE, DEPLOYED_INSTANCE):
        config = load(path)
        assert isinstance(config, dict) and config, f"{path} parsed to {config!r}"


@pytest.mark.parametrize("block", SHARED_BLOCKS)
def test_the_two_instances_agree_on_every_shared_block(block):
    """Compared by parsed value, not by text: the point is that the instances
    behave alike, and the comments around each block are deliberately different
    (one explains a laptop, the other a deployment)."""
    laptop, deployed = load(LAPTOP_INSTANCE), load(DEPLOYED_INSTANCE)
    assert block in laptop, f"{LAPTOP_INSTANCE.name} lost its `{block}` block"
    assert block in deployed, f"{DEPLOYED_INSTANCE.name} lost its `{block}` block"
    assert laptop[block] == deployed[block], (
        f"`{block}` differs between the two instance configs. Dagster has no include, "
        "so they are copies; change both or say here why they may diverge."
    )


def test_one_run_at_a_time_survives_in_both():
    """The shared-block test above would pass if both files dropped to Dagster's
    default together. This is the value itself, and the reason it is not a
    parameter: DuckDB takes one writer, so ten concurrent runs is not a tuning
    choice (docs/ORCHESTRATION.md, the dagster-graph-and-jobs skill)."""
    for path in (LAPTOP_INSTANCE, DEPLOYED_INSTANCE):
        assert load(path)["concurrency"]["runs"]["max_concurrent_runs"] == 1, (
            f"{path} no longer queues runs one at a time"
        )


def test_only_the_deployed_instance_configures_storage():
    """The laptop's storage is Dagster's default — SQLite beside its config —
    and it must stay that way: a `storage:` block there would make `just dagster`
    on a fresh clone need a database."""
    laptop, deployed = load(LAPTOP_INSTANCE), load(DEPLOYED_INSTANCE)
    for block in DEPLOYED_ONLY:
        assert block not in laptop, (
            f"`{block}` appeared in {LAPTOP_INSTANCE.name}; the laptop instance needs no services"
        )
        assert block in deployed, f"{DEPLOYED_INSTANCE.name} lost its `{block}` block"
    assert deployed["storage"]["postgres"]["postgres_db"]["port"] == 5432


def test_every_deployed_storage_value_but_the_port_comes_from_the_environment():
    """`deploy/dagster.yaml` is baked into the image read-only and is the same
    for every deployment, so a literal hostname or password in it is either a
    secret in the tree or a value that cannot be changed without a rebuild. The
    port is the exception: it is Postgres's, not a deployment's."""
    deployed = load(DEPLOYED_INSTANCE)
    for block in DEPLOYED_ONLY:
        for path, value in leaves(deployed[block], (block,)):
            if path[-1] in ("module", "class", "port"):
                continue
            assert isinstance(value, dict) and set(value) == {"env"}, (
                f"{'.'.join(path)} is the literal {value!r}; write it as `{{env: NAME}}`"
            )


def env_example_names() -> set[str]:
    """Every variable `.env.example` assigns, commented or not.

    Commented counts: the whole file is commented, because an uncommented
    `.env.example` copied to `.env` would configure a stack nobody asked for.
    """
    return {
        line.lstrip("#").split("=", 1)[0].strip()
        for line in (REPO_ROOT / ".env.example").read_text().splitlines()
        if "=" in line and line.lstrip("#").lstrip()[:1].isupper()
    }


def compose_service(name: str) -> dict:
    return load(COMPOSE)["services"][name]


def dockerfile_env_names() -> set[str]:
    """Names the image sets with `ENV`. A continuation-line form is what this
    Dockerfile uses, so every `NAME=` on any line of such a block counts."""
    names, in_env = set(), False
    for raw in DOCKERFILE.read_text().splitlines():
        line = raw.strip()
        if line.startswith("ENV "):
            in_env, line = True, line[4:].strip()
        elif not in_env:
            continue
        for token in line.rstrip("\\").split():
            if "=" in token and token.split("=", 1)[0].isupper():
                names.add(token.split("=", 1)[0])
        in_env = raw.rstrip().endswith("\\")
    return names


def test_every_variable_the_deployed_instance_reads_is_assigned_somewhere():
    """A `{env: X}` the instance reads and nothing assigns is a
    `DagsterInvalidConfigError` at startup. Loud, but the fix is to set it
    somewhere, and these three files are where this repo says what there is to
    set: `.env.example` for a person, `compose.yaml` for the stack, the
    Dockerfile for what is true of the image itself."""
    assigned = env_example_names() | set(compose_service(DAGSTER_SERVICE)["environment"])
    assigned |= dockerfile_env_names()
    missing = sorted(env_names(load(DEPLOYED_INSTANCE)) - assigned)
    assert not missing, f"{DEPLOYED_INSTANCE.name} reads {missing}, which nothing assigns"


def test_every_name_the_run_launcher_copies_is_set_on_the_service():
    """`env_vars` is a copy list: a bare `NAME` tells `DockerRunLauncher` to take
    that variable from its *own* environment, and `parse_env_var` raises when it
    is unset. That happens inside the daemon, when the run is dequeued — after
    the UI has already reported it launched — so it is a red run with a stack
    trace nowhere near the cause.

    The Dockerfile counts too: `PROJECT_ROOT` and friends are `ENV` in the image,
    which is the launcher's environment as much as compose's block is.
    """
    launcher = load(DEPLOYED_INSTANCE)["run_launcher"]["config"]
    service_env = set(compose_service(DAGSTER_SERVICE)["environment"])
    available = service_env | dockerfile_env_names()
    copied = [name for name in launcher["env_vars"] if "=" not in name]
    missing = sorted(set(copied) - available)
    assert not missing, (
        f"the run launcher copies {missing}, which the `{DAGSTER_SERVICE}` service does not set"
    )


def test_the_run_containers_mount_the_same_volumes_at_the_same_paths():
    """A run container is launched by `dagster_docker`, not by compose, so its
    volume list is written out by hand in `deploy/dagster.yaml`. Every source
    must be a volume this file declares by `name:` — compose's
    `<project>_<volume>` prefixing does not apply to a container it did not
    create — and every mount must land where the service has it, or a run writes
    a warehouse nobody reads."""
    compose = load(COMPOSE)
    declared = {v["name"] for v in compose["volumes"].values()}
    service_mounts = dict(
        m.split(":")[:2]
        for m in compose_service(DAGSTER_SERVICE)["volumes"]
        if not m.startswith("/")
    )
    for mount in load(DEPLOYED_INSTANCE)["run_launcher"]["config"]["container_kwargs"]["volumes"]:
        source, path = mount.split(":")[:2]
        assert source in declared, (
            f"run containers mount `{source}`, which compose.yaml does not declare"
        )
        assert service_mounts.get(source) == path, (
            f"`{source}` is at {path} in a run container and "
            f"{service_mounts.get(source)} in the `{DAGSTER_SERVICE}` service"
        )


def test_the_launcher_network_is_the_compose_network():
    """A run container off the compose network cannot resolve `postgres` or
    `seaweedfs`, and fails at the first attach."""
    network = load(DEPLOYED_INSTANCE)["run_launcher"]["config"]["network"]
    assert network == load(COMPOSE)["networks"]["default"]["name"]


def test_runs_launch_from_the_service_image_and_not_a_tag():
    """A tag moves on `just compose-build`; the service keeps its image until
    `just compose-up`. Stock `DockerRunLauncher` launches runs by name, so in
    between they ran code the service was not running. The subclass looks up
    the launching container's image ID instead, which is the service's own.

    Everything a name could come from is asserted absent, because the subclass
    ignores them and a value left behind would read as if it mattered. And the
    service must keep its default hostname: that is the container ID the
    subclass looks itself up by.
    """
    launcher = load(DEPLOYED_INSTANCE)["run_launcher"]
    assert (launcher["module"], launcher["class"]) == (
        "modern_data_stack.docker_launcher",
        "ServiceImageDockerRunLauncher",
    )
    assert "image" not in launcher["config"]
    service = compose_service(DAGSTER_SERVICE)
    assert "DAGSTER_CURRENT_IMAGE" not in service["environment"]
    assert "hostname" not in service, (
        "a `hostname:` on the service replaces the container ID the launcher "
        "uses to find its own image"
    )


def image_tags() -> list[tuple[str, str, int]]:
    """Every image this repo names, with where it came from and how many numeric
    components its version must have. See the test below for why those differ."""
    tags = [
        (s["image"], "compose.yaml", 2) for s in load(COMPOSE)["services"].values() if "image" in s
    ]
    for dockerfile in (DOCKERFILE, DEVCONTAINER_DOCKERFILE):
        tags += [
            (line.split()[1], str(dockerfile.relative_to(REPO_ROOT)), 3)
            for line in dockerfile.read_text().splitlines()
            if line.startswith("FROM ")
        ]
    return tags


def test_every_image_tag_is_pinned():
    """No `latest`, no bare name, no floating alias. Dependabot watches these —
    `docker-compose` for compose.yaml, `docker` for both Dockerfiles — and a
    moving tag is one it cannot bump, which is the "unwatched pin" that
    `docs/RUNNING_AS_A_SERVICE.md` §2 predicted a container would add.

    **The two minimums differ because upstream's release schemes do.** The
    Dockerfile's images are language runtimes, where `X.Y.Z` is the exact tag
    and `X.Y` is an alias that moves under you — `python:3.13-slim-bookworm`
    silently becomes the next patch. The compose services are `postgres:17.11`
    and `chrislusf/seaweedfs:4.47`, whose exact tags have two components, so
    three cannot be required of them.

    **What this cannot catch**, deliberately and not by oversight: a two-part
    compose tag that upstream publishes as an alias. Nothing in a tag string
    says whether it moves, so a new compose image wants a look at how its
    publisher tags releases. `mds:local` is skipped — it is built here, not
    pulled.
    """
    for tag, source, minimum in image_tags():
        if tag == "mds:local":
            continue
        _, _, version = tag.partition(":")
        assert version, f"`{tag}` ({source}) names no tag, so it floats with `latest`"
        assert version != "latest", f"`{tag}` ({source}) is pinned to `latest`"
        numeric = re.match(r"\d+(?:\.\d+)*", version)
        assert numeric, f"`{tag}` ({source}) does not start with a version"
        parts = len(numeric.group().split("."))
        assert parts >= minimum, (
            f"`{tag}` ({source}) has {parts} version component(s); {minimum} are needed for an "
            "exact tag there — see this test's docstring"
        )


def dockerfile_image_version(name: str) -> str:
    """The version part of the Dockerfile's `FROM <name>:<version>…` line."""
    for tag, source, _ in image_tags():
        if source == "Dockerfile" and tag.partition(":")[0] == name:
            numeric = re.match(r"\d+(?:\.\d+)*", tag.partition(":")[2])
            assert numeric, f"the Dockerfile's `FROM {tag}` does not start with a version"
            return numeric.group()
    raise AssertionError(f"the Dockerfile has no `FROM {name}:…` line")


def test_the_base_images_match_their_other_pins():
    """The Python image carries `.python-version`'s minor, and the Node image
    `pages.yml`'s `node-version` major. Each pair is one version stated twice,
    and Dependabot's `docker` entry ignores the bumps that would split them
    (see its comment in `.github/dependabot.yml`), so the partner moves by hand
    in the same PR. A split Python fails the image build at `uv sync`, since
    `UV_PYTHON_DOWNLOADS=never`; a split Node builds the site on a runtime the
    published site never saw."""
    python_pin = PYTHON_VERSION.read_text().strip()
    python_image = dockerfile_image_version("python")
    assert python_image.split(".")[:2] == python_pin.split(".")[:2], (
        f"the Dockerfile's `python:{python_image}` is not on `.python-version`'s {python_pin}"
    )

    node_pin = re.search(r"node-version:\s*[\"']?(\d+)", PAGES_WORKFLOW.read_text())
    assert node_pin, "pages.yml no longer sets a `node-version` this test can read"
    node_image = dockerfile_image_version("node")
    assert node_image.split(".")[0] == node_pin.group(1), (
        f"the Dockerfile's `node:{node_image}` is not on pages.yml's Node {node_pin.group(1)}"
    )


def test_the_dev_container_carries_the_service_images_toolchain():
    """`.devcontainer/Dockerfile` copies uv and Node out of the same images the
    root Dockerfile does, so a reviewer's container and the service run one
    toolchain. Each tag is stated twice; Dependabot's `docker` entry lists both
    directories, so its grouped PR moves the pair together, and this catches a
    hand edit to one of them or the directory falling out of that list."""
    tags = image_tags()

    def version(name: str, source: str) -> str:
        return next(t.partition(":")[2] for t, s, _ in tags if s == source and t.startswith(name))

    for name in ("node:", "ghcr.io/astral-sh/uv:"):
        assert version(name, ".devcontainer/Dockerfile") == version(name, "Dockerfile"), (
            f"the two Dockerfiles copy from different `{name}` tags"
        )
    assert "/.devcontainer" in DEPENDABOT.read_text(), (
        "dependabot.yml's `docker` entry no longer lists /.devcontainer, so its base images "
        "are pins nothing watches"
    )
