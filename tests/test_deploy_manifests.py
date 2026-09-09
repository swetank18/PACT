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

import pytest
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


def test_no_manifest_claims_a_target_is_undeployed_that_the_docs_say_is_live():
    """
    A deployment status written into a file that nothing re-reads.

    `render.yaml` carried "NOT DEPLOYED — no Render credentials here" for four
    days after Render was deployed, and `scripts/deploy.sh` carried the same
    line until a commit fixed it there and missed this one. Both were written
    when they were true. Nobody re-reads a header comment, which is exactly why
    it is the kind of claim that rots: the file it sits in is correct, so
    nothing about it fails.

    This asserts the negative rather than the positive — that no manifest says
    "not deployed" while README.md and HANDOFF.md give a live URL. Whether the
    instance is up is a question for `scripts/deploy.sh check`, which makes a
    request; this is about the tree contradicting itself.
    """
    docs = (REPO / "README.md").read_text() + (REPO / "HANDOFF.md").read_text()
    live = re.findall(r"https://([a-z0-9-]+\.onrender\.com)", docs)
    if not live:
        pytest.skip("no deployed Render URL is claimed anywhere, so there is nothing to contradict")

    for name in ("render.yaml", "scripts/deploy.sh"):
        text = (REPO / name).read_text()
        assert not re.search(r"NOT DEPLOYED|not been deployed|no Render credentials", text), (
            f"{name} says the Render target is undeployed, but the docs give "
            f"{live[0]} as live. One of them is stale."
        )


def test_the_deck_generators_dependency_is_declared_and_stays_out_of_the_image():
    """
    Both halves matter, and they pull in opposite directions.

    `scripts/gen_hacksummit_deck.py` and `gen_explainer_deck.py` import
    `python-pptx`, and both are documented commands. It was declared nowhere, so
    either of them was an ImportError on a clean checkout — the deck is
    generated precisely so a number that moves can be carried into it, and that
    is worth nothing if the generator does not run.

    The obvious fix is the wrong one. The Dockerfile installs requirements.txt
    into the runtime image, and python-pptx brings lxml and Pillow with it: some
    30 MB added to a 208 MB image, for a tool that never runs in a container.
    So it lives in requirements-docs.txt, and this asserts it stays there.
    """
    runtime = (REPO / "requirements.txt").read_text()
    docs = (REPO / "requirements-docs.txt").read_text()

    assert re.search(r"^python-pptx==", docs, re.M), (
        "requirements-docs.txt no longer pins python-pptx, so the deck "
        "generators have an undeclared dependency again"
    )
    assert not re.search(r"^python-pptx", runtime, re.M), (
        "python-pptx is in requirements.txt, which the Dockerfile installs into "
        "the runtime image. It is only needed to build the decks — put it back "
        "in requirements-docs.txt"
    )

    for name in ("gen_hacksummit_deck.py", "gen_explainer_deck.py"):
        source = (REPO / "scripts" / name).read_text()
        assert "from pptx" in source or "import pptx" in source, (
            f"scripts/{name} no longer imports pptx, so this split is stale"
        )


def test_the_build_identity_is_wired_from_ci_through_to_healthz():
    """
    Four files have to agree or a running instance cannot say what it is.

    The deployed instance spent four days and eleven fixes behind `main`, and
    answering "is what is deployed what I think is deployed" meant fetching its
    stylesheet and grepping for a CSS class one of those fixes had added. The
    image now carries its revision and `/healthz` reports it.

    That only stays true while the chain does: CI passes the SHA, compose
    forwards it as a build arg, the Dockerfile declares the ARG and promotes it
    to an ENV, and the app reads that ENV. Break any link and nothing fails —
    the field just quietly reads "source" on a real deployment, which is worse
    than no field at all because it looks like an answer.
    """
    workflow = (REPO / ".github" / "workflows" / "container.yml").read_text()
    compose = yaml.safe_load((REPO / "docker-compose.yml").read_text())
    dockerfile = (REPO / "Dockerfile").read_text()
    app = (REPO / "deploy" / "app.py").read_text()

    assert re.search(r"PACT_BUILD_REV:\s*\$\{\{\s*github\.sha\s*\}\}", workflow), (
        "container.yml no longer passes the commit SHA into the build"
    )
    assert "PACT_BUILD_AT" in workflow, "container.yml no longer stamps a build time"

    args = compose["services"]["pact"]["build"]["args"]
    for name in ("PACT_BUILD_REV", "PACT_BUILD_AT"):
        assert name in args, f"docker-compose.yml does not forward {name}"
        assert f"${{{name}" in str(args[name]), (
            f"docker-compose.yml hard-codes {name} instead of taking it from the "
            f"environment, so CI's value would be ignored"
        )
        assert re.search(rf"^ARG {name}=", dockerfile, re.M), (
            f"the Dockerfile does not declare ARG {name}"
        )
        assert f"{name}=${name}" in dockerfile, (
            f"the Dockerfile declares ARG {name} but never promotes it to an ENV, "
            f"so it is gone by the time the app runs"
        )
        assert f'os.environ.get("{name}"' in app, (
            f"deploy/app.py does not read {name}"
        )

    assert '"build": {"rev": BUILD_REV, "at": BUILD_AT}' in app, (
        "/healthz no longer reports the build"
    )

    # The ARGs must come after the dependency install, or every new commit
    # invalidates the pip layer and CI builds from scratch each push.
    assert dockerfile.index("ARG PACT_BUILD_REV") > dockerfile.index(
        "RUN pip install --no-cache-dir -r requirements.txt"
    ), "the build ARGs sit above the pip layer, so every commit busts that cache"
