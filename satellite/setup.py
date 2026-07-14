"""Idempotent seeding of Satellite's built-in service catalog (spec/28).

Runs on migrate so a fresh Satellite always has its handlers registered. Adding a new
service = add a row here (or create a Service in Desk) pointing handler_path at a class
with apply(vm, binding)/withdraw(vm, binding); no core Atlas change, ever.
"""

from __future__ import annotations

import frappe

# The built-in service catalog. Host-plane concerns (mesh, gateway) stay in Atlas per
# the guest-plane-only boundary rule (spec/28); these are the guest-plane services.
DEFAULT_SERVICES: list[dict] = [
	{
		"service_key": "routing",
		"title": "Self-service routing",
		"handler_path": "satellite.services.routing.RoutingService",
	},
	{
		"service_key": "routing-proxy",
		"title": "Edge proxy",
		"handler_path": "satellite.services.routing.RoutingProxyService",
	},
	{
		"service_key": "site",
		"title": "Site deploy",
		"handler_path": "satellite.services.site.SiteService",
	},
]


def ensure_default_services() -> None:
	for spec in DEFAULT_SERVICES:
		if not frappe.db.exists("Service", spec["service_key"]):
			frappe.get_doc({"doctype": "Service", **spec}).insert(ignore_permissions=True)
	_seed_denylist()


def _seed_denylist() -> None:
	"""Seed the brand/keyword subdomain denylist (spec/18 Component H). Idempotent; the
	operator curates the DocType from this floor."""
	from satellite.satellite.doctype.subdomain_denylist.subdomain_denylist import seed_denylist

	seed_denylist()
