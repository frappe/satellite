"""Registration — mirror the VMs/Servers an Atlas provisions into Satellite (spec/28).

A webhook (or the reconcile sweep) tells Satellite "VM X on Atlas A changed"; Satellite
pulls the full record off that Atlas's read API and upserts its local mirror, keyed by
(atlas, remote_id). The mirror carries the SSH targets a service handler needs (host
IPv4, guest IPv6) so no handler ever calls Atlas itself.
"""

from __future__ import annotations

import frappe

from satellite.atlas_client import AtlasClient


def handle_event(atlas: str, event: str, remote_id: str) -> None:
	"""Background job: apply one lifecycle webhook. Registration is idempotent; a
	deregister removes the mirror (and cascades to its Service Bindings)."""
	if event == "vm.deregistered":
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
	return doc.name


def deregister_vm(atlas: str, remote_id: str) -> None:
	name = frappe.db.exists("Virtual Machine", {"atlas": atlas, "remote_id": remote_id})
	if name:
		frappe.delete_doc("Virtual Machine", name, force=1, ignore_permissions=True)


def reconcile(atlas: str) -> int:
	"""Backstop sweep: pull every VM off an Atlas and upsert. Heals a missed webhook.
	Returns the count synced."""
	rows = AtlasClient(atlas).list_virtual_machines()
	for payload in rows:
		_upsert_vm(atlas, payload)
	return len(rows)
