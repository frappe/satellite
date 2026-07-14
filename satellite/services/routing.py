"""Routing service — Satellite reconciles each edge proxy's live maps over run_guest.

The controller side of the proxy design (spec/12 §7, spec/17): Satellite is the source
of truth, each proxy guest's `lua_shared_dict` is a cache. Build the desired regional
maps from the routing doctypes, serialize them the SAME canonical way the guest's
persist modules emit (so "in sync?" is a byte compare), SSH into each proxy GUEST over
its public IPv6, read its live maps, and bulk-sync on drift. The Atlas provisioner is
no longer in this path — Satellite drives it over its own key.

Two service handlers (spec/28 catalog rows):
  routing-proxy — a VM that IS an edge proxy. apply reconciles it into the fleet;
                  withdraw is a no-op (the next reconcile simply stops targeting it,
                  since `_proxy_vms` reads the live Applied bindings). Its `config`
                  carries `{public_ipv4, region}` (the reserved v4 for the wildcard).
  routing       — a bench VM that participates in self-service routing. apply is a
                  no-op (the guest API resolves callers by source /128); withdraw tears
                  down that VM's routes (its Subdomain / Custom Domain / Port Mapping
                  rows) and reconciles, so a deregistered VM leaves no dangling route.

Four maps are reconciled per proxy in one pass: the wildcard subdomain map (http
`/sync`), the custom-domain :443 SNI map (stream-admin `SYNC-SNI`), the custom-domain
:80 ACME map (http `/acme/sync`), and the TCP port map (stream-admin `SYNC`).
"""

from __future__ import annotations

import json
import shlex

import frappe

from satellite.routing.desired import (
	canonical_json,
	custom_domain_acme_map,
	custom_domain_sni_map,
	subdomain_map,
)
from satellite.routing.ports import port_map
from satellite.ssh import run_guest

# Paths mirror the stock Ubuntu `nginx` package layout on the proxy guest (spec/12).
ADMIN_SOCKET = "/run/nginx/admin.sock"
ADMIN_BASE = "http://localhost"
# The stream{}-side line-protocol client build.sh installs on the proxy guest's PATH
# (the L4 analogue of `curl --unix-socket`): GET/SYNC for the TCP port map, GET-SNI/
# SYNC-SNI for the custom-domain :443 SNI map.
STREAM_ADMIN_BIN = "stream-admin"

PROXY_SERVICE = "routing-proxy"


# ---------------------------------------------------------------------------
# Enqueue — the one deduplicated fleet reconcile every routing change triggers
# ---------------------------------------------------------------------------


def enqueue_reconcile() -> None:
	"""Background-reconcile the whole proxy fleet. A reconcile reads the WHOLE desired
	state (all four maps), so it is the same job no matter which routing row triggered
	it — N changes need one reconcile, not N. `deduplicate` collapses a burst to a
	single queued job; `enqueue_after_commit` so the job sees this change's committed
	rows. queue=long because it SSHes into every proxy."""
	frappe.enqueue(
		"satellite.services.routing.reconcile_fleet",
		queue="long",
		timeout=300,
		job_id="routing_reconcile",
		deduplicate=True,
		enqueue_after_commit=True,
	)


# ---------------------------------------------------------------------------
# Service handlers
# ---------------------------------------------------------------------------


class RoutingProxyService:
	"""A VM that is an edge proxy. Binding it adds the VM to the fleet and pushes the
	current maps; unbinding it drops it from the reconcile targets."""

	def apply(self, vm, binding) -> None:
		"""Reconcile this freshly-bound proxy so it serves the current maps immediately."""
		reconcile_proxy(vm.name)

	def withdraw(self, vm, binding) -> None:
		"""No effect to undo: `_proxy_vms` reads the live Applied `routing-proxy`
		bindings, so once this binding is gone the next reconcile stops targeting it."""


class RoutingService:
	"""A bench VM that participates in self-service routing (the guest API). apply is a
	no-op — the API resolves callers by source /128, not by binding. withdraw is the
	single teardown point: delete this VM's routes so a deregistered VM leaves nothing
	dangling in the served maps."""

	def apply(self, vm, binding) -> None:
		pass

	def withdraw(self, vm, binding) -> None:
		teardown_vm_routes(vm.name)


def teardown_vm_routes(virtual_machine: str) -> None:
	"""Delete every routing row a VM owns (Subdomain / Custom Domain / Port Mapping) and
	reconcile the fleet. Idempotent — a VM with no routes is a clean no-op. The single
	place routing teardown lives (mirrors Atlas's terminate-deletes-subdomains)."""
	deleted = False
	for doctype in ("Subdomain", "Custom Domain", "Port Mapping"):
		for name in frappe.get_all(doctype, filters={"virtual_machine": virtual_machine}, pluck="name"):
			frappe.delete_doc(doctype, name, force=1, ignore_permissions=True)
			deleted = True
	if deleted:
		enqueue_reconcile()


# ---------------------------------------------------------------------------
# Fleet reconcile
# ---------------------------------------------------------------------------


def reconcile_fleet() -> list[str]:
	"""Reconcile every proxy VM to the desired maps. Returns the names of the proxies
	that were synced (any of the four maps drifted). Each proxy holds the WHOLE map, so
	they all get the same bodies. A proxy that can't be reached is logged and skipped —
	one wedged guest never wedges the loop (spec/12 §7.3)."""
	desired = _desired_maps()
	synced = []
	for vm_name in _proxy_vms():
		try:
			if _reconcile_proxy(vm_name, desired):
				synced.append(vm_name)
		except Exception as exception:
			frappe.log_error(f"Proxy reconcile failed for {vm_name}: {exception}", "Proxy reconcile")
	return synced


def reconcile_proxy(virtual_machine: str) -> bool:
	"""Reconcile a single proxy VM to the desired maps. Returns True iff a sync was
	needed (any map had drifted)."""
	return _reconcile_proxy(virtual_machine, _desired_maps())


def _desired_maps() -> dict[str, str]:
	"""The four canonical map bodies a proxy must serve, built once per reconcile run
	(the same for every proxy in the fleet). Each is serialized the SAME canonical way
	the matching guest persist module emits, so each "in sync?" check is a byte
	compare."""
	return {
		"sites": canonical_json(subdomain_map()),
		"sni": canonical_json(custom_domain_sni_map()),
		"acme": canonical_json(custom_domain_acme_map()),
		"ports": canonical_json(port_map()),
	}


def read_live_maps(virtual_machine: str) -> dict:
	"""Read all four of a proxy guest's live maps in one pass and return each alongside
	the desired body + a drift flag — the read-only twin of `_reconcile_proxy` (same
	reads, no writes). A read failure raises: a view that silently showed an empty map
	would lie about what the proxy serves."""
	desired = _desired_maps()
	reads = {
		"sites": _curl_command("GET", "/map"),
		"sni": f"{STREAM_ADMIN_BIN} GET-SNI",
		"acme": _curl_command("GET", "/acme"),
		"ports": f"{STREAM_ADMIN_BIN} GET",
	}
	out: dict = {}
	for key, command in reads.items():
		live_json, stderr, code = run_guest(virtual_machine, command, timeout=60)
		if code != 0:
			frappe.throw(f"Reading the {key} map from {virtual_machine} failed (exit {code}): {stderr[-300:]}")
		out[key] = {
			"live": json.loads(live_json),
			"desired": json.loads(desired[key]),
			"in_sync": live_json == desired[key],
		}
	return out


def _reconcile_proxy(virtual_machine: str, desired: dict[str, str]) -> bool:
	"""Reconcile all four maps on one proxy. Each map is read-then-synced independently
	(read live, byte-compare, sync on drift), so an unchanged map costs one read and no
	write. Returns True iff any map drifted and was synced."""
	specs = (
		# key,   read command,                    write command
		("sites", _curl_command("GET", "/map"), _curl_command("POST", "/sync", data_stdin=True)),
		("sni", f"{STREAM_ADMIN_BIN} GET-SNI", f"{STREAM_ADMIN_BIN} SYNC-SNI"),
		("acme", _curl_command("GET", "/acme"), _curl_command("POST", "/acme/sync", data_stdin=True)),
		("ports", f"{STREAM_ADMIN_BIN} GET", f"{STREAM_ADMIN_BIN} SYNC"),
	)
	drifted = False
	for key, read, write in specs:
		drifted |= _sync_map(virtual_machine, key, read=read, write=write, desired_json=desired[key])
	return drifted


def _sync_map(virtual_machine: str, key: str, *, read: str, write: str, desired_json: str) -> bool:
	"""Read a proxy guest's live map, byte-compare against the desired canonical body,
	and bulk-sync on drift. Returns True iff a sync was needed. Both sides serve/accept
	the SAME canonical bytes, so the compare is exact. Idempotent, self-healing,
	rebuild-safe (spec/12 §7.2)."""
	live_json, _stderr, _code = run_guest(virtual_machine, read, timeout=60)
	if live_json == desired_json:
		return False
	stdout, stderr, code = run_guest(virtual_machine, write, timeout=120, stdin=desired_json)
	if code != 0:
		frappe.throw(f"{key} sync to {virtual_machine} failed (exit {code}): {stderr[-500:]}")
	# A non-zero exit alone is not enough for the stream-admin maps: the client ALWAYS
	# exits 0 and reports a line-protocol error in stdout, so a sync the proxy REJECTED
	# would otherwise read as success and leave the live map stale. Reject only an
	# explicit error reply — the stream `error: …` token and the http `{"error": …}`
	# body both carry it — and let either success shape ("ok" / `{"synced": true}`)
	# through (curl --fail-with-body already turns an http admin error into a non-zero
	# exit, caught above).
	if stdout.lstrip().startswith("error") or '"error"' in stdout:
		frappe.throw(f"{key} sync to {virtual_machine} was rejected: {stdout.strip()[:500]}")
	return True


def _curl_command(method: str, path: str, data_stdin: bool = False) -> str:
	"""The guest-side `curl --unix-socket` invocation for the http admin. With
	data_stdin the body is read from the SSH stdin stream (`--data-binary @-`)."""
	parts = ["curl", "-s", "--fail-with-body", "--unix-socket", ADMIN_SOCKET, "-X", method]
	if data_stdin:
		parts += ["--data-binary", "@-"]
	parts.append(f"{ADMIN_BASE}{path}")
	return " ".join(shlex.quote(p) for p in parts)


def _proxy_vms() -> list[str]:
	"""Every LIVE VM bound as a `routing-proxy` (an Applied binding). These are the
	reconcile targets; each gets the full map. A Terminated VM is excluded — its guest is
	gone and its /128 no longer answers."""
	names = frappe.get_all(
		"Service Binding",
		filters={"service": PROXY_SERVICE, "binding_status": "Applied"},
		pluck="virtual_machine",
	)
	return [
		vm for vm in names if frappe.db.get_value("Virtual Machine", vm, "vm_status") != "Terminated"
	]
