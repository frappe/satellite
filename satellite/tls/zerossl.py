"""ZeroSSL TLS provider — registered stub.

ZeroSSL also speaks ACME, so the eventual implementation is close to
`LetsEncryptProvider` with a different directory URL and EAB credentials. Not built;
registered only so the Select option resolves and `for_tls_provider_type` returns a
clear "not implemented" rather than "no implementation". Ported from Atlas.
"""

from __future__ import annotations

import frappe
from frappe import _

from satellite.dns.base import DnsProvider
from satellite.tls import register
from satellite.tls.base import AuthResult, IssuedCert, TlsProvider


@register
class ZeroSslProvider(TlsProvider):
	provider_type = "ZeroSSL"
	caa_issuer = "sectigo.com"  # ZeroSSL certs chain to Sectigo's CAA identity

	def authenticate(self) -> AuthResult:
		return AuthResult(ok=False, error="ZeroSSL is not implemented yet")

	def issue(self, domain: str, dns_provider: DnsProvider) -> IssuedCert:
		frappe.throw(_("ZeroSSL issuance is not implemented yet"))
