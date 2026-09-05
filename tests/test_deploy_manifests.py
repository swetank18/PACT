"""
The deployment manifests, cross-checked against each other and the image.

Neither `fly.toml` nor `render.yaml` has ever been executed — there are no Fly
or Render credentials here — so nothing has ever told us whether they agree with
the image they deploy. Every other claim in this repository is checked by
running something; these two are checked by reading, which is the weakest kind
of verification and the reason this file exists.

What a drift here costs, concretely:

    a port that disagrees      the health check never passes, the deploy rolls
                               back, and the logs say only "unhealthy"
    a mount path that          the machine boots, works, and issues a NEW
    disagrees                  signing key on every restart — so every headroom
                               envelope a merchant is already holding stops
                               verifying, silently. Nothing goes red.
    two instances              two SQLite files, two different answers to "how
                               much budget is left", which is the one question
                               this system exists to answer correctly

None of those fail loudly at the venue. All three are one line in a config file.
"""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parent.parent

PORT = "8080"
VOLUME = "/data"
HEALTH_PATH = "/healthz"


def dockerfile_env() -> dict[str, str]:
    """The ENV block of the runtime stage, which is where the defaults live.

    Parsed rather than imported because these values only exist inside the
    image: nothing in Python reads the Dockerfile, so a typo in it is invisible
    to every other test in this suite.
    """
    text = (REPO / "Dockerfile").read_text()
    env: dict[str, str] = {}
    for match in re.finditer(r"^ENV\s+(.*?)(?=^\w|\Z)", text, re.M | re.S):
        block = match.group(1).replace("\\\n", " ")
        for pair in re.finditer(r"([A-Z_][A-Z0-9_]*)=(\S+)", block):
            env[pair.group(1)] = pair.group(2)
    return env


def fly() -> dict:
    return tomllib.loads((REPO / "fly.toml").read_text())


def render() -> dict:
    return yaml.safe_load((REPO / "render.yaml").read_text())["services"][0]


def render_env() -> dict[str, str]:
    return {
        var["key"]: str(var["value"])
        for var in render()["envVars"]
        if "value" in var
    }


def compose() -> dict:
    return yaml.safe_load((REPO / "docker-compose.yml").read_text())["services"]["pact"]


# ------------------------------------------------------------------- ports ---


def test_every_manifest_agrees_on_the_port():
    """One disagreement here is a health check that never passes."""
    assert f"EXPOSE {PORT}" in (REPO / "Dockerfile").read_text()
    assert f"--port\", \"{PORT}\"" in (REPO / "Dockerfile").read_text()
    assert dockerfile_env()["PORT"] == PORT
    assert fly()["http_service"]["internal_port"] == int(PORT)
    assert fly()["env"]["PORT"] == PORT
    assert render_env()["PORT"] == PORT
    assert f"{PORT}:{PORT}" in compose()["ports"]


def test_every_manifest_agrees_on_the_health_check_path():
    assert HEALTH_PATH in (REPO / "Dockerfile").read_text()
    assert fly()["http_service"]["checks"][0]["path"] == HEALTH_PATH
    assert render()["healthCheckPath"] == HEALTH_PATH
    assert HEALTH_PATH in yaml.safe_dump(compose()["healthcheck"])


# ------------------------------------------------------------ the volume ---


def test_the_database_and_the_signing_key_live_on_the_mounted_volume():
    """
    The failure this catches does not go red anywhere.

    A machine whose database and signing key are written inside the container
    rather than on the volume boots, serves, and passes its health check. It
    also forgets every mandate and issues a new signing key on every restart —
    and a merchant holding an envelope signed by the old key cannot tell a
    rotated key from a forgery.
    """
    for source, env in (
        ("Dockerfile", dockerfile_env()),
        ("fly.toml", fly()["env"]),
        ("render.yaml", render_env()),
    ):
        assert env["PACT_DB_URL"] == f"sqlite:///{VOLUME}/pact.db", source
        assert env["PACT_GATE_KEY_PATH"].startswith(f"{VOLUME}/"), source

    assert fly()["mounts"]["destination"] == VOLUME
    assert render()["disk"]["mountPath"] == VOLUME
    assert f"{VOLUME}" in " ".join(compose()["volumes"])


def test_compose_inherits_the_paths_rather_than_restating_them():
    """
    docker-compose.yml deliberately sets neither PACT_DB_URL nor
    PACT_GATE_KEY_PATH: the image already points both at /data, and a second
    copy of a path is a second place for it to drift. If someone adds one, it
    must still agree — that is what this asserts, rather than forbidding it.
    """
    environment = compose().get("environment") or {}
    for key in ("PACT_DB_URL", "PACT_GATE_KEY_PATH"):
        if key in environment:
            assert str(environment[key]) == dockerfile_env()[key], key


# ---------------------------------------------------------- one instance ---


def test_neither_target_runs_more_than_one_instance():
    """
    Two instances is two databases and two answers to how much budget is left.

    The ledger's correctness rests on SQLite's write lock inside one process;
    the rail's idempotency table is in memory; the saga is a background task.
    Scaling this means Postgres and a queue, and until then a replica count
    above one is a correctness bug rather than a capacity decision.
    """
    assert render()["numInstances"] == 1
    assert fly()["http_service"]["min_machines_running"] == 1
    # A machine that stops when the last request finishes stops running the
    # reservation sweeper and the reconciler, and drops every open SSE stream.
    assert fly()["http_service"]["auto_stop_machines"] is False
    assert '"--workers", "1"' in (REPO / "Dockerfile").read_text()


# ------------------------------------------------------------- the image ---


def test_both_targets_deploy_the_image_ci_publishes():
    """
    Not a rebuild. What ships must be the artefact the six beats ran against,
    otherwise "CI proves the image works" says nothing about what is deployed.
    """
    image = "ghcr.io/swetank18/pact:latest"
    assert fly()["build"]["image"] == image
    assert render()["image"]["url"] == image


def test_no_live_credential_is_baked_into_a_manifest():
    """
    Test keys only, and the secrets are marked unsynced rather than written
    down. `sync: false` is Render's way of saying "set this in the dashboard";
    a value here would be a credential in a public repository.
    """
    for var in render()["envVars"]:
        if var["key"] in (
            "RAZORPAY_KEY_ID",
            "RAZORPAY_KEY_SECRET",
            "RAZORPAY_WEBHOOK_SECRET",
            "ANTHROPIC_API_KEY",
        ):
            assert var.get("sync") is False and "value" not in var, var["key"]

    for path in ("fly.toml", "render.yaml", "docker-compose.yml"):
        text = (REPO / path).read_text()
        assert "rzp_live_" not in text, path
        # A test key is still a key. Neither of these belongs in a file that is
        # pushed, and one of them has been in this repository's history before.
        assert not re.search(r"rzp_test_\w", text), path
        assert not re.search(r"sk-ant-\w", text), path
