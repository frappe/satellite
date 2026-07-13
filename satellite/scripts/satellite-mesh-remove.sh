#!/usr/bin/env bash
# satellite-mesh-remove — withdraw a satellite-managed VM from its host's
# private-mesh registry on terminate. The teardown half of satellite-mesh-add, run
# by Atlas's runner on the host through the exposed run_host_script (spec/28 §3B).
# Idempotent: a VM with no line (already gone, or a host that never had it) is a
# clean no-op.
#
# Variables arrive as environment: VIRTUAL_MACHINE_NAME.
set -euo pipefail

peers_file="${SATELLITE_MESH_DIR:-/etc/satellite/mesh}/peers"

if [ -f "${peers_file}" ]; then
	grep -v "^${VIRTUAL_MACHINE_NAME} " "${peers_file}" >"${peers_file}.tmp" || true
	mv "${peers_file}.tmp" "${peers_file}"
fi

echo "ATLAS_RESULT={\"removed\": \"${VIRTUAL_MACHINE_NAME}\"}"
