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

from pathlib import Path

import pytest

yaml = pytest.importorskip("yaml")

REPO_ROOT = Path(__file__).resolve().parent.parent
LAPTOP_INSTANCE = REPO_ROOT / ".dagster/dagster.yaml"
DEPLOYED_INSTANCE = REPO_ROOT / "deploy/dagster.yaml"

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


def test_every_variable_the_deployed_instance_reads_is_in_the_env_example():
    """`.env.example` is this repo's list of what there is to set, and `just`
    loads the `.env` beside it into every recipe. A variable the instance reads
    and that file never names is one a reader can only find by reading YAML.

    Commented lines count: the whole file is commented, because an uncommented
    `.env.example` copied to `.env` would configure a stack nobody asked for.
    """
    example = (REPO_ROOT / ".env.example").read_text()
    assigned = {
        line.lstrip("#").split("=", 1)[0].strip()
        for line in example.splitlines()
        if "=" in line and line.lstrip("#").lstrip()[:1].isupper()
    }
    missing = sorted(env_names(load(DEPLOYED_INSTANCE)) - assigned)
    assert not missing, f"{DEPLOYED_INSTANCE.name} reads {missing}, which .env.example never names"
