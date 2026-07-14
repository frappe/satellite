"""Idempotent seeding of Satellite's built-in service catalog (spec/28).

Runs on migrate so a fresh Satellite always has its handlers registered. Adding a new
service = add a row here (or create a Service in Desk) pointing handler_path at a class
with apply(vm, binding)/withdraw(vm, binding); no core Atlas change, ever.
"""

from __future__ import annotations

import frappe

DEFAULT_SERVICES = [
	{
		"service_key": "mesh",
		"title": "Private mesh",
		"handler_path": "satellite.services.mesh.MeshService",
	},
]


def ensure_default_services() -> None:
	for spec in DEFAULT_SERVICES:
		if not frappe.db.exists("Service", spec["service_key"]):
			frappe.get_doc({"doctype": "Service", **spec}).insert(ignore_permissions=True)
