"""The TCP port pool, allocation, and the desired port map (spec/17-tcp-proxy).

The L4 twin of the subdomain map: a `Port Mapping` reserves a public port from the
pool and forwards it to a backend VM's service port. Ported from Atlas's
`Port Mapping` doctype module. The pool is a Satellite-wide default today (it must
match the proxy's pre-opened `listen 10000-19999;` range); a configurable pool moves
to a Satellite Settings single with Phase 5.
"""

from __future__ import annotations

import frappe

# The TCP port pool, matching the proxy's pre-opened `listen 10000-19999;` range
# (spec/17-tcp-proxy). Growing it is a deliberate proxy snapshot roll.
PORT_POOL: tuple[int, int] = (10000, 19999)


def port_pool() -> tuple[int, int]:
	"""The TCP port pool as an inclusive (low, high) range."""
	return PORT_POOL


def allocate_port() -> int:
	"""The lowest port in the pool not already held by ANY mapping — active or inactive.
	An inactive row still owns its port (toggling it back on must not collide), so both
	count as taken. Pool exhaustion is a typed throw, never a silent wrap.

	Serialized under a row lock: SELECT the existing mappings FOR UPDATE so concurrent
	allocators queue behind each other. The `{protocol}-{public_port}` unique name is the
	final backstop for the first-row-in-an-empty-pool race (one insert wins, the other
	retries)."""
	low, high = port_pool()
	taken = {
		row["public_port"]
		for row in frappe.qb.from_("Port Mapping").for_update().select("public_port").run(as_dict=True)
		if row["public_port"] is not None
	}
	for port in range(low, high + 1):
		if port not in taken:
			return port
	frappe.throw(
		f"TCP port pool exhausted: all {high - low + 1} ports ({low}-{high}) are allocated. "
		"Grow the pool (a deliberate proxy snapshot roll, spec/17-tcp-proxy)."
	)


def port_map() -> dict[str, str]:
	"""The desired port→backend map: every ACTIVE mapping, as
	`{"<public_port>": "[<address>]:<target_port>"}`. The value is a ready-to-dial
	bracketed-v6 host:port literal so the guest does no formatting. The full map every
	proxy VM serves (spec/17 "each proxy holds the whole map")."""
	rows = frappe.get_all(
		"Port Mapping", filters={"active": 1}, fields=["public_port", "address", "target_port"]
	)
	return {str(row["public_port"]): f"[{row['address']}]:{row['target_port']}" for row in rows}
