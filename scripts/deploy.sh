#!/usr/bin/env bash
# Deploy, then check that what came up is the thing that was tested.
#
#     ./scripts/deploy.sh fly                 # needs flyctl and a Fly login
#     ./scripts/deploy.sh render              # prints what to do; see below
#     ./scripts/deploy.sh check https://…     # smoke an instance already up
#
# Neither target has ever been deployed from here — there are no credentials in
# this environment — so this script is the part of that job that *can* be
# written down: the preflight, the one command, and the check afterwards. It
# refuses clearly rather than half-deploying when something is missing.
#
# Two things it deliberately does NOT do:
#
#   It does not run the six demo beats against the deployed instance. They write
#   orders, force a stockout and deliver webhooks; that is fine on a laptop and
#   is not something to do to a public URL by reflex. Pass --beats if you mean
#   it.
#
#   It does not create the volume. `fly volumes create` is a one-off with a
#   region and a size in it, and a script that guesses either would be a script
#   that silently makes a second one.
set -euo pipefail
cd "$(dirname "$0")/.."

PY=.venv/bin/python
[[ -x "$PY" ]] || PY="$(command -v python3)"

mode="${1:-}"
shift || true
beats=""
[[ "${1:-}" == "--beats" ]] && { beats="yes"; shift; }

die() { echo "error: $*" >&2; exit 1; }

preflight() {
  # The manifests are checked by reading them, because nothing has ever run
  # them. Better here than at the venue.
  echo "── preflight"
  env -u PYTHONPATH PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 "$PY" -m pytest \
    tests/test_deploy_manifests.py -q
}

smoke() {
  local base="$1"
  echo "── smoke: $base"
  if [[ -n "$beats" ]]; then
    "$PY" scripts/smoke.py --base "$base" --timeout 120
  else
    # Health, mounts, the console bundle, the parity vector, and that the
    # signing key is NOT served. Everything that does not write.
    "$PY" scripts/smoke.py --base "$base" --skip-beats --timeout 120
    echo
    echo "   beats not run. Re-run with --beats if you want them against $base."
  fi
}

case "$mode" in
  fly)
    command -v flyctl >/dev/null 2>&1 || command -v fly >/dev/null 2>&1 \
      || die "flyctl is not installed. https://fly.io/docs/flyctl/install/"
    FLY="$(command -v flyctl || command -v fly)"
    "$FLY" auth whoami >/dev/null 2>&1 || die "not logged in: $FLY auth login"

    preflight

    # The volume must exist first, and it is not this script's job to guess a
    # region or a size. Without it the machine boots, works, and issues a new
    # signing key on every restart.
    if ! "$FLY" volumes list 2>/dev/null | grep -q pact_data; then
      die "no pact_data volume. Create it once, with the region you want:
       $FLY volumes create pact_data --size 1 --region bom"
    fi

    echo "── deploying the image CI published, not a rebuild"
    "$FLY" deploy --config fly.toml "$@"

    host="$("$FLY" status --json 2>/dev/null | "$PY" -c \
      'import json,sys; print(json.load(sys.stdin).get("Hostname",""))')"
    [[ -n "$host" ]] || die "deployed, but could not read the hostname from fly status"
    smoke "https://${host}"
    ;;

  render)
    preflight

    # There is an API, and with a key this does not have to be a dashboard
    # action at all. `render.py` sends exactly what render.yaml describes and
    # refuses to send anything it does not.
    if [[ -n "${RENDER_API_KEY:-}" ]]; then
      "$PY" scripts/render.py "${1:-create}" "${@:2}"
      exit $?
    fi

    cat <<'TEXT'

── No RENDER_API_KEY set, so this is a dashboard action.

   With a key it is not:  RENDER_API_KEY=rnd_… ./scripts/deploy.sh render create

   1. render.com → New → Blueprint, point it at this repository.
   2. It reads render.yaml: one instance, a 1 GB disk at /data, the image
      ghcr.io/swetank18/pact:latest.
   3. The four secrets are marked `sync: false`, so set them in the dashboard or
      leave them unset. Unset runs the mock_upi rail, which exercises every line
      above the adapter. Test keys only.
   4. Then check what came up:

          ./scripts/deploy.sh check https://<your-service>.onrender.com

   The GHCR package must be public, or Render needs registry credentials.
TEXT
    ;;

  check)
    base="${1:-}"
    [[ -n "$base" ]] || die "usage: ./scripts/deploy.sh check https://…"
    smoke "${base%/}"
    ;;

  *)
    sed -n '2,22p' "$0"
    exit 2
    ;;
esac
