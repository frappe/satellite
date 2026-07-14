"""Bench Routing Audit — the append-only forensic log of every guest routing call.

MyISAM (declared in the JSON `engine`) so each insert auto-commits per statement,
independent of the request transaction: a rejected `register` `frappe.throw`s and rolls
back its InnoDB work, but the audit row it wrote first survives (audit-before-throw).
Written by `satellite.routing.api._audit` on EVERY path of EVERY endpoint — the
trust-root story lives here (the source /128 that tried, and the forwarded chain whose
divergence from it is the hijack signal). Never edited; read-only forensic.
"""

from __future__ import annotations

from frappe.model.document import Document


class BenchRoutingAudit(Document):
	# begin: auto-generated types
	# This code is auto-generated. Do not modify anything in this block.

	from typing import TYPE_CHECKING

	if TYPE_CHECKING:
		from frappe.types import DF

		business_reject: DF.Check
		endpoint: DF.Data | None
		fwd_headers: DF.SmallText | None
		label: DF.Data | None
		request_body: DF.SmallText | None
		source_ip: DF.Data | None
		status: DF.Data | None
		vm: DF.Data | None
	# end: auto-generated types

	pass
