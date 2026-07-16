"""Registration — mirror the VMs/Servers an Atlas provisions into Satellite (spec/28).

A webhook (or the reconcile sweep) tells Satellite "VM X on Atlas A changed"; Satellite
pulls the full record off that Atlas's read API and upserts its local mirror, keyed by
(atlas, remote_id). The mirror carries the SSH targets a service handler needs (host
IPv4, guest IPv6) so no handler ever calls Atlas itself.
"""

from __future__ import annotations

import frappe

from satellite.atlas_client import AtlasClient


def handle_event(atlas: str, vm_event: str, remote_id: str) -> None:
	"""Background job: apply one lifecycle webhook. Registration is idempotent; a
	deregister removes the mirror (and cascades to its Service Bindings).

	The param is `vm_event`, not `event`: this runs as a `frappe.enqueue` job, and
	`enqueue` reserves the kwarg `event` for its own queue-event category — a kwarg named
	`event` would be consumed by enqueue and never arrive here."""
	if vm_event == "vm.deregistered":
		deregister_vm(atlas, remote_id)
	else:  # vm.registered / vm.updated
		register_vm(atlas, remote_id)


def register_vm(atlas: str, remote_id: str) -> str:
	"""Pull one VM off its Atlas and upsert the mirror row. Returns the mirror name."""
	return _upsert_vm(atlas, AtlasClient(atlas).get_virtual_machine(remote_id))


def _upsert_vm(atlas: str, payload: dict) -> str:
	values = {
		"vm_status": payload.get("status"),
		"server": payload.get("server"),
		"server_ipv4": payload.get("server_ipv4"),
		"guest_ipv6": payload.get("guest_ipv6"),
		"tenant": payload.get("tenant"),
		"private_address": payload.get("private_address"),
		"build_mode": payload.get("build_mode"),
		"warm": 1 if payload.get("warm") else 0,
	}
	name = frappe.db.exists("Virtual Machine", {"atlas": atlas, "remote_id": payload["name"]})
	if name:
		doc = frappe.get_doc("Virtual Machine", name)
		doc.update(values)
		doc.save(ignore_permissions=True)
	else:
		doc = frappe.get_doc(
			{"doctype": "Virtual Machine", "atlas": atlas, "remote_id": payload["name"], **values}
		).insert(ignore_permissions=True)
	# A Terminated guest can no longer serve — tear down its routes so the proxy stops
	# targeting a dead /128. Driven off the mirrored status, so both the lifecycle webhook
	# and the reconcile sweep heal it (idempotent: a VM with no routes is a no-op). This is
	# the guest-plane analogue of Atlas's synchronous terminate-deletes-subdomains.
	if values["vm_status"] == "Terminated":
		from satellite.services.routing import teardown_vm_routes

		teardown_vm_routes(doc.name)
	return doc.name


def deregister_vm(atlas: str, remote_id: str) -> None:
	name = frappe.db.exists("Virtual Machine", {"atlas": atlas, "remote_id": remote_id})
	if name:
		# Delete the routes BEFORE the mirror so nothing dangles (the Subdomain/Custom
		# Domain/Port Mapping rows Link the mirror; a bare force-delete would orphan them).
		from satellite.services.routing import teardown_vm_routes

		teardown_vm_routes(name)
		frappe.delete_doc("Virtual Machine", name, force=1, ignore_permissions=True)


def reconcile(atlas: str) -> int:
	"""Backstop sweep: pull every VM off an Atlas and upsert. Heals a missed webhook.
	Returns the count synced."""
	rows = AtlasClient(atlas).list_virtual_machines()
	for payload in rows:
		_upsert_vm(atlas, payload)
	return len(rows)
