"""The active Region Domain — the single wildcard suffix routing FQDNs are built
under (Satellite's Phase-2 stand-in for Atlas's `active_root_domain`).

Satellite is single-region today, so there is exactly one active `Region Domain`. Both
the guest routing API (to build `<label>.<domain>`) and the proxy reconcile (to scope
the on-guest cert dir) resolve the suffix here. Fail loud when none or several are
active — every region-dependent path needs an unambiguous answer (Taste 17).
"""

from __future__ import annotations

import frappe
from frappe import _


def active_region_domain():
	"""The single active `Region Domain` doc. Raises when none or several are active —
	routing, like the image/server choice, must be unambiguous."""
	active = frappe.get_all(
		"Region Domain",
		filters={"is_active": 1},
		fields=["name"],
		limit=2,
		ignore_permissions=True,
	)
	if not active:
		frappe.throw(_("No region domain is active — ask your operator to activate one."))
	if len(active) > 1:
		frappe.throw(_("Several region domains are active — ask your operator to activate a single one."))
	return frappe.get_doc("Region Domain", active[0]["name"])


def region_suffix() -> str:
	"""The active region's wildcard suffix (e.g. `blr1.frappe.dev`) — the string a
	site's FQDN is `<label>.<suffix>`."""
	return active_region_domain().domain
