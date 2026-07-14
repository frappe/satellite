"""The desired maps a proxy fleet serves, built from the routing doctypes.

Atlas is no longer in this path: Satellite owns the `Subdomain` / `Custom Domain`
tables and builds the canonical map bodies the proxy reconcile compares against each
guest's live map. `canonical_json` is byte-identical to the guest's persist output
(sorted keys, 2-space indent, trailing newline) so "in sync?" is a plain string
compare — the exact contract Atlas's `proxy.canonical_json` held (spec/12 §4.3, §7.2).

`routing_address` is the shared denormalization every routing row runs at save: the
target VM's routing /128, read off the Satellite mirror (`guest_ipv6`, falling back to
the private mesh `private_address` for a dark VM with no public /128). A VM with
neither is a hard error — an unaddressable target can't be a routing destination.
"""

from __future__ import annotations

import json

import frappe


def canonical_json(site_map: dict[str, str]) -> str:
	"""The one canonical serialization of a routing map, byte-identical to the guest's
	persist output: sorted keys, 2-space indent, one key per line, trailing newline.
	Because both sides emit the same bytes, the reconcile "in sync?" check is a plain
	string compare — no semantic diff (spec/12 §4.3, §7.2)."""
	return json.dumps(site_map, sort_keys=True, indent=2) + "\n"


def routing_address(virtual_machine: str) -> str:
	"""The target VM's routing /128, read off the Satellite mirror. Prefers the public
	`guest_ipv6`; falls back to the private mesh `private_address` for a dark VM with no
	public /128. Raises if the VM has neither — an unaddressable target can't be a
	routing destination."""
	vm = frappe.db.get_value(
		"Virtual Machine", virtual_machine, ["guest_ipv6", "private_address"], as_dict=True
	)
	if not vm:
		frappe.throw(f"Virtual Machine {virtual_machine} is not mirrored; cannot map a route to it")
	address = vm.guest_ipv6 or vm.private_address
	if not address:
		frappe.throw(
			f"Virtual Machine {virtual_machine} has no guest_ipv6 or private_address; "
			"cannot map a route to it"
		)
	return address


def subdomain_map() -> dict[str, str]:
	"""The desired subdomain→address map: every ACTIVE subdomain. This is the full map
	every proxy VM serves (spec/12 "each proxy holds the whole map"). Values are the
	target VM's bare routing /128; the proxy dials `[<address>]:80`."""
	rows = frappe.get_all("Subdomain", filters={"active": 1}, fields=["subdomain", "address"])
	return {row["subdomain"]: row["address"] for row in rows}


def custom_domain_sni_map() -> dict[str, str]:
	"""The desired :443 SNI passthrough map: every active custom domain, as
	`host -> "[<v6>]:443"` ready-to-dial literals. The stream-side `domains` dict the
	proxy's `ssl_preread` router looks up to pass the raw TLS stream through to the
	backend VM's `:443` (the bench terminates TLS with its own cert). A domain enters
	this map the moment it is registered — there is no readiness gate."""
	rows = frappe.get_all("Custom Domain", filters={"active": 1}, fields=["domain", "address"])
	return {row["domain"]: f"[{row['address']}]:443" for row in rows}


def custom_domain_acme_map() -> dict[str, str]:
	"""The desired :80 ACME-passthrough map: every active custom domain, as
	`host -> "[<v6>]"` bracketed bare-v6 literals. The http-side `acme_domains` dict the
	proxy's `:80` Host fork looks up to forward a custom domain's HTTP-01 challenge to
	its VM. Same row set as the :443 SNI map, differing only in value shape (the acme
	router appends `:80`)."""
	rows = frappe.get_all("Custom Domain", filters={"active": 1}, fields=["domain", "address"])
	return {row["domain"]: f"[{row['address']}]" for row in rows}
