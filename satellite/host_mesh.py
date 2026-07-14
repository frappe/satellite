"""Cross-host WireGuard mesh reconcile, computed from the Satellite mirror (spec/28
Phase 1). Ported from atlas/atlas/host_mesh.py; the DESIGN is unchanged (each host peers
with every OTHER Active host; a peer's AllowedIPs = every /128 resident on it + its own
infra mesh /128), but the inputs are the mirror rows and the transport is Satellite's own
host SSH (`run_host_addr`, since a host may have zero resident VMs to resolve through).

CONVERGING: a skipped push is a partition, so per-host failures are collected and re-raised.
The PURE computation (`_active_hosts`, `_residents_by_host`, `render_wg_mesh_config`) is the
unit-tested surface; the SSH push cannot be verified on a single host (an N=1 mesh has no
peers) — that needs a real multi-host fleet.
"""

from __future__ import annotations

import shlex

import frappe

from satellite.networking import (
	WG_HOST_PORT,
	WIREGUARD_MTU,
	derive_client_address,
	derive_host_mesh_address,
	derive_host_wireguard_keypair,
	derive_private_address,
)
from satellite.ssh import run_host_addr

MESH_DEVICE = "wg-mesh"
MESH_CONFIG_PATH = "/etc/wireguard/wg-mesh.conf"
MESH_KEY_PATH = "/etc/atlas-host-mesh.key"

# A Terminated VM released its /128; a Draft was never placed. Everything else keeps its
# /128 advertised so a stop/start does not churn the mesh.
_RESIDENT_EXCLUDED_STATUSES = ("Terminated", "Draft")


def reconcile_host_mesh(atlas: str) -> list[str]:
	"""Reconcile every host of one Atlas's mirror to current fleet state. Collects
	per-host failures and re-raises (converging — a missed push is a partition)."""
	hosts = _active_hosts(atlas)
	residents = _residents_by_host(atlas, hosts)
	synced, failures = [], []
	for host in hosts:
		try:
			if _reconcile_one_host(host, hosts, residents):
				synced.append(host["name"])
		except Exception as exception:
			failures.append((host["name"], exception))
	if failures:
		detail = "; ".join(f"{name}: {error}" for name, error in failures)
		frappe.throw(f"Host-mesh reconcile incomplete: {detail}")
	return synced


def _active_hosts(atlas: str) -> list[dict]:
	"""Every mirror Server (for this Atlas) with an ipv6 mesh endpoint — the peer universe.
	Endpoint/pubkey/mesh-address are DERIVED from the remote Server UUID. A host with no
	ipv6 (unplaced / Fake) is skipped, so the reconcile is a clean no-op on a test fleet."""
	rows = frappe.get_all(
		"Server",
		filters={"atlas": atlas},
		fields=["name", "remote_id", "ipv4", "ipv6", "server_status"],
	)
	hosts = []
	for row in rows:
		if not row.ipv6 or row.server_status in ("Archived", "Broken"):
			continue
		_private_key, public_key = derive_host_wireguard_keypair(row.remote_id)
		hosts.append(
			{
				"name": row.name,  # mirror name (atlas-remoteid) — the SSH-independent handle
				"remote_id": row.remote_id,  # Atlas Server UUID — the derivation key
				"ipv4": row.ipv4,  # Satellite's SSH target
				"endpoint": row.ipv6,  # the wg endpoint
				"public_key": public_key,
				"mesh_address": derive_host_mesh_address(row.remote_id),
			}
		)
	return hosts


def _residents_by_host(atlas: str, hosts: list[dict]) -> dict[str, list[str]]:
	"""Map each host's mirror name -> the /128s resident on it: every non-terminated mirror
	VM on that server, all tenants mixed. Residency keys on `server` (the remote server id)
	so a dark VM with no guest_ipv6 still counts; the /128 is derived from (tenant, remote_id)."""
	by_remote = {host["remote_id"]: host["name"] for host in hosts}
	rows = frappe.get_all(
		"Virtual Machine",
		filters={
			"atlas": atlas,
			"server": ["in", list(by_remote)],
			"vm_status": ["not in", _RESIDENT_EXCLUDED_STATUSES],
		},
		fields=["remote_id", "server", "tenant"],
	)
	residents: dict[str, list[str]] = {host["name"]: [] for host in hosts}
	for row in rows:
		if not row.tenant:
			continue  # no tenant -> no derivable /48 -> not on the private plane
		residents[by_remote[row.server]].append(derive_private_address(row.tenant, row.remote_id))
	_add_customer_vpc_clients(hosts, residents)
	return residents


def _add_customer_vpc_clients(hosts: list[dict], residents: dict[str, list[str]]) -> None:
	"""Fold every Active VPN Peer's client /128 into its gateway host's AllowedIPs (the
	return path). A no-op with no gateway/peers; fail-open if the doctype is absent (the
	gateway may not have been installed yet)."""
	if not frappe.db.exists("DocType", "VPN Peer"):
		return
	peers = frappe.get_all("VPN Peer", filters={"status": "Active"}, fields=["name", "tenant", "gateway"])
	if not peers:
		return
	gateway_remote_server = {
		gateway: frappe.db.get_value("Virtual Machine", gateway, "server")
		for gateway in {peer.gateway for peer in peers if peer.gateway}
	}
	by_remote = {host["remote_id"]: host["name"] for host in hosts}
	for peer in peers:
		host = by_remote.get(gateway_remote_server.get(peer.gateway))
		if not host or not peer.tenant:
			continue
		residents[host].append(derive_client_address(peer.tenant, peer.name))


def render_wg_mesh_config(this_host: str, hosts: list[dict], residents: dict[str, list[str]]) -> str:
	"""The /etc/wireguard/wg-mesh.conf body for `this_host` (a mirror name): one [Peer] per
	OTHER host, AllowedIPs = the resident /128s + the peer's own mesh /128. Canonical bytes
	(peers sorted by pubkey, /128s sorted) so drift is a plain string compare. The private
	key is referenced by PATH on the host, never inlined here."""
	lines = ["[Interface]", f"ListenPort = {WG_HOST_PORT}", ""]
	for peer in sorted(hosts, key=lambda host: host["public_key"]):
		if peer["name"] == this_host:
			continue
		allowed = sorted(
			[f"{address}/128" for address in residents.get(peer["name"], [])]
			+ [f"{peer['mesh_address']}/128"]
		)
		lines += [
			"[Peer]",
			f"PublicKey = {peer['public_key']}",
			f"AllowedIPs = {', '.join(allowed)}",
			f"Endpoint = [{peer['endpoint']}]:{WG_HOST_PORT}",
			"PersistentKeepalive = 25",
			"",
		]
	return "\n".join(lines) + "\n"


def _reconcile_one_host(host: dict, hosts: list[dict], residents: dict[str, list[str]]) -> bool:
	"""Read `host`'s live wg-mesh peer set, compare to desired, push on drift. Returns True
	iff a push was needed. NOTE: the SSH push is unverifiable on a single host."""
	desired = render_wg_mesh_config(host["name"], hosts, residents)
	if _live_peer_config(host, hosts) == desired and _mesh_address_present(host):
		return False
	_push_wg_mesh(host, desired)
	return True


def _mesh_address_present(host: dict) -> bool:
	stdout, _stderr, code = run_host_addr(host["ipv4"], f"ip -6 addr show dev {MESH_DEVICE}", timeout=30)
	return code == 0 and host["mesh_address"] in stdout


def _live_peer_config(host: dict, hosts: list[dict]) -> str:
	"""Read `wg show wg-mesh dump` over host SSH and re-render into render_wg_mesh_config's
	byte shape, so a byte diff == drift. Empty/unreachable -> "" (first reconcile pushes)."""
	stdout, _stderr, code = run_host_addr(host["ipv4"], f"sudo wg show {MESH_DEVICE} dump", timeout=60)
	if code != 0:
		return ""
	lines = [line for line in stdout.rstrip("\n").split("\n") if line.strip()]
	by_pubkey = {peer["public_key"]: peer for peer in hosts}
	rendered = ["[Interface]", f"ListenPort = {WG_HOST_PORT}", ""]
	live: dict[str, list[str]] = {}
	for raw in lines[1:]:  # line 0 is the interface itself
		fields = raw.split("\t")
		allowed = fields[3] if len(fields) > 3 else ""
		live[fields[0]] = sorted(
			part.strip() for part in allowed.split(",") if part.strip() and part.strip() != "(none)"
		)
	for public_key in sorted(live):
		peer = by_pubkey.get(public_key)
		endpoint = f"[{peer['endpoint']}]:{WG_HOST_PORT}" if peer else "?"
		rendered += [
			"[Peer]",
			f"PublicKey = {public_key}",
			f"AllowedIPs = {', '.join(live[public_key])}",
			f"Endpoint = {endpoint}",
			"PersistentKeepalive = 25",
			"",
		]
	return "\n".join(rendered) + "\n"


def _push_wg_mesh(host: dict, desired: str) -> None:
	private_key, _public_key = derive_host_wireguard_keypair(host["remote_id"])
	_write_host_file(host, MESH_KEY_PATH, private_key + "\n", mode="0600")
	_write_host_file(host, MESH_CONFIG_PATH, desired, mode="0600")
	stdout, stderr, code = run_host_addr(
		host["ipv4"], f"sudo bash -c {shlex.quote(_apply_script(host['mesh_address']))}", timeout=120
	)
	if code != 0:
		frappe.throw(f"wg syncconf to host {host['name']} failed (exit {code}): {stderr[-500:]}")
	_ = stdout


def _apply_script(mesh_address: str) -> str:
	"""Create-or-heal wg-mesh, assert MTU/address/up/route, syncconf the peers, THEN set the
	key + listen port LAST (syncconf clears an unmentioned key — order is load-bearing)."""
	return (
		f"set -e; "
		f"if ! ip link show {MESH_DEVICE} >/dev/null 2>&1; then "
		f"ip link add dev {MESH_DEVICE} type wireguard; fi; "
		f"ip link set dev {MESH_DEVICE} mtu {WIREGUARD_MTU}; "
		f"ip -6 addr replace {mesh_address}/128 dev {MESH_DEVICE}; "
		f"ip link set dev {MESH_DEVICE} up; "
		f"ip -6 route replace fdaa::/16 dev {MESH_DEVICE}; "
		f"wg syncconf {MESH_DEVICE} <(wg-quick strip {MESH_CONFIG_PATH}); "
		f"wg set {MESH_DEVICE} private-key {MESH_KEY_PATH} listen-port {WG_HOST_PORT}"
	)


def _write_host_file(host: dict, path: str, content: str, *, mode: str) -> None:
	"""Write via tee (content on stdin, never in argv), then chmod."""
	command = f"sudo tee {shlex.quote(path)} >/dev/null && sudo chmod {mode} {shlex.quote(path)}"
	_stdout, stderr, code = run_host_addr(host["ipv4"], command, timeout=60, stdin=content)
	if code != 0:
		frappe.throw(f"Writing {path} to host {host['name']} failed (exit {code}): {stderr[-300:]}")
