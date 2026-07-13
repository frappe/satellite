#!/usr/bin/env bash
# satellite-mesh-add — publish a satellite-managed VM onto its host's private-mesh
# registry. The host-plane effect a VM's provision drives through Atlas's exposed
# run_host_script (spec/28 §3B): satellite ships this verb, Atlas stages and runs it
# on the host over the host's IPv4 (satellite opens no SSH itself). A minimal,
# idempotent stand-in for the WireGuard host mesh's per-host AllowedIPs — one line
# per resident VM (name + mesh address) in /etc/satellite/mesh/peers.
#
# Variables arrive as environment (the runner invokes shell verbs as
# `env VAR=val bash -x <file>`): VIRTUAL_MACHINE_NAME, MESH_PEER.
set -euo pipefail

# Production writes /etc/satellite/mesh; SATELLITE_MESH_DIR overrides it so a test
# (or a faithful-double local host) can point the registry at a scratch dir.
peers_dir="${SATELLITE_MESH_DIR:-/etc/satellite/mesh}"
peers_file="${peers_dir}/peers"

mkdir -p "${peers_dir}"
touch "${peers_file}"

# Idempotent: drop any prior line for this VM, then append the current one.
grep -v "^${VIRTUAL_MACHINE_NAME} " "${peers_file}" >"${peers_file}.tmp" || true
mv "${peers_file}.tmp" "${peers_file}"
printf '%s %s\n' "${VIRTUAL_MACHINE_NAME}" "${MESH_PEER}" >>"${peers_file}"

echo "ATLAS_RESULT={\"peer\": \"${VIRTUAL_MACHINE_NAME}\", \"count\": $(wc -l <"${peers_file}" | tr -d ' ')}"
