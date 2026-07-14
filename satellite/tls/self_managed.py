"""Self-Managed TLS provider — operator drops PEMs at the configured paths.

The escape hatch for when Satellite should not run an ACME client: the operator issues
the wildcard cert out of band and places the PEMs at the cert paths. `issue()` acquires
nothing — it throws, directing the operator to Push to Proxies instead. Ported from Atlas.
"""

from __future__ import annotations

import frappe
from frappe import _

from satellite.dns.base import DnsProvider
from satellite.tls import register
from satellite.tls.base import AuthResult, IssuedCert, TlsProvider


@register
class SelfManagedTlsProvider(TlsProvider):
	provider_type = "Self-Managed"

	def authenticate(self) -> AuthResult:
		return AuthResult(ok=True, account_label="self-managed")

	def issue(self, domain: str, dns_provider: DnsProvider) -> IssuedCert:
		frappe.throw(
			_(
				"Self-Managed TLS does not issue certificates. Place fullchain.pem and "
				"privkey.pem at the TLS Certificate's paths, then use Push to Proxies."
			)
		)
