"""Registration — mirror the VMs/Servers an Atlas provisions into Satellite (spec/28).

A webhook (or the reconcile sweep) tells Satellite "VM X on Atlas A changed"; Satellite
pulls the full record off that Atlas's read API and upserts its local mirror, keyed by
(atlas, remote_id). The mirror carries the SSH targets a service handler needs (host
IPv4, guest IPv6) so no handler ever calls Atlas itself.
"""

from __future__ import annotations

import json

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
		address_changed = bool(values["guest_ipv6"]) and doc.guest_ipv6 != values["guest_ipv6"]
		doc.update(values)
		doc.save(ignore_permissions=True)
		# A migration re-addressed the guest (new /128). The routing rows carry a
		# denormalized address (desired.routing_address), so re-save them to follow the
		# mirror, then reconcile — replacing Atlas's deleted migration._repoint_routes.
		if address_changed:
			_rederive_routes(doc.name)
	else:
		doc = frappe.get_doc(
			{"doctype": "Virtual Machine", "atlas": atlas, "remote_id": payload["name"], **values}
		).insert(ignore_permissions=True)
	# A Terminated guest can no longer serve — tear down its routes so the proxy stops
	# targeting a dead /128. Driven off the mirrored status, so both the lifecycle webhook
	# and the reconcile sweep heal it (idempotent: a VM with no routes is a no-op). This is
	# the guest-plane analogue of Atlas's synchronous terminate-deletes-subdomains.
	if values["vm_status"] == "Terminated":
		_teardown_vm(doc.name)
	else:
		_reconcile_intent_subdomains(doc.name, payload.get("routing_subdomains"))
	return doc.name


def _teardown_vm(virtual_machine: str) -> None:
	"""A Terminated guest can no longer serve — delete its routes AND its Service Bindings so
	nothing keeps targeting or CLASSIFYING a dead /128 (a lingering `routing-proxy` binding
	would keep `is_proxy()` true for a recycled address). Idempotent; heals off the mirrored
	status via the webhook or the reconcile sweep."""
	from satellite.services.routing import teardown_vm_routes

	teardown_vm_routes(virtual_machine)
	for name in frappe.get_all("Service Binding", filters={"virtual_machine": virtual_machine}, pluck="name"):
		frappe.delete_doc("Service Binding", name, force=1, ignore_permissions=True)


def _reconcile_intent_subdomains(virtual_machine: str, labels) -> None:
	"""Reconcile a VM's PROVISIONER-INTENT routing Subdomains to `labels` — the seam that
	lets Atlas's Site/Pilot record their subdomain(s) ON THE VM (read-API `routing_subdomains`,
	a list or JSON string) instead of creating the Subdomain themselves (the routing dedup).

	A two-way set reconcile over `provisioner_intent` rows only: create the missing, delete
	the dropped (e.g. an attached Pilot that detached). Guest self-serve routes (not marked
	intent) are never touched. Skipped until the mirror is addressable — `routing_address`
	throws without a guest_ipv6/private_address, and the first `vm.registered` can arrive
	pre-addressing; the Running `vm.updated` (or the sweep) heals it. A label already held by
	ANOTHER VM is LOGGED, not silently swallowed — an unroutable Site must surface, never
	vanish. The Subdomain's own after_insert reconciles the fleet."""
	desired = set(labels if isinstance(labels, list) else json.loads(labels)) if labels else set()
	vm = frappe.db.get_value(
		"Virtual Machine", virtual_machine, ["guest_ipv6", "private_address"], as_dict=True
	)
	if not (vm and (vm.guest_ipv6 or vm.private_address)):
		return

	current = {
		row.subdomain: row.name
		for row in frappe.get_all(
			"Subdomain",
			filters={"virtual_machine": virtual_machine, "provisioner_intent": 1},
			fields=["name", "subdomain"],
		)
	}
	for label, name in current.items():
		if label not in desired:
			frappe.delete_doc("Subdomain", name, force=1, ignore_permissions=True)
	for label in desired - set(current):
		holder = frappe.db.get_value("Subdomain", {"subdomain": label}, "virtual_machine")
		if holder:
			if holder != virtual_machine:
				frappe.log_error(
					f"Routing intent for {virtual_machine} wants subdomain {label!r}, held by "
					f"{holder} — Site left unrouted",
					"Routing intent conflict",
				)
			continue
		frappe.get_doc(
			{
				"doctype": "Subdomain",
				"subdomain": label,
				"virtual_machine": virtual_machine,
				"active": 1,
				"provisioner_intent": 1,
			}
		).insert(ignore_permissions=True)


def _rederive_routes(virtual_machine: str) -> None:
	"""Re-save every routing row a VM owns so its denormalized address re-derives from the
	(now-updated) mirror, then reconcile the fleet. Idempotent — a VM with no routes is a
	no-op. The Satellite twin of Atlas's migration._repoint_routes (which is deleted once
	routing lives here)."""
	changed = False
	for doctype in ("Subdomain", "Custom Domain", "Port Mapping"):
		for name in frappe.get_all(doctype, filters={"virtual_machine": virtual_machine}, pluck="name"):
			frappe.get_doc(doctype, name).save(ignore_permissions=True)
			changed = True
	if changed:
		from satellite.services.routing import enqueue_reconcile

		enqueue_reconcile()


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
