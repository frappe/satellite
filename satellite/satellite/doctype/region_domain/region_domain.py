"""Region Domain — the regional wildcard zone the proxy fleet terminates.

Satellite's Phase-2 stand-in for Atlas's `Root Domain`: it ties a region to the
wildcard suffix a bench site's routing FQDN is built under (`<label>.<domain>`). The
guest routing API and the proxy reconcile both resolve the region suffix from the
single active row (`active_region_domain`, in `satellite.routing.region`). Phase 5
grows this into the full `Root Domain` (TLS provider, DNS zone) — for now it carries
only what routing needs: the domain, its region, and the active flag.
"""

from __future__ import annotations

import frappe
from frappe.model.document import Document


class RegionDomain(Document):
	# begin: auto-generated types
	# This code is auto-generated. Do not modify anything in this block.

	from typing import TYPE_CHECKING

	if TYPE_CHECKING:
		from frappe.types import DF

		dns_provider_type: DF.Literal["Route53"]
		domain: DF.Data
		is_active: DF.Check
		region: DF.Data
		tls_provider_type: DF.Literal["Let's Encrypt", "ZeroSSL", "Self-Managed"]
	# end: auto-generated types

	def validate(self) -> None:
		"""The domain is the routing suffix — store it lowercased and dot-trimmed so the
		FQDNs the API builds are canonical, and a stray trailing dot never splits the key."""
		self.domain = (self.domain or "").strip().lower().rstrip(".")
		self.region = (self.region or "").strip()
