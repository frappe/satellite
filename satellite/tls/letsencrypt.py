"""Let's Encrypt TLS provider — ACME DNS-01 via certbot, run on the Satellite node.

Reads `Lets Encrypt Settings` (ACME directory, account email) and issues `*.<domain>`
by driving `tls.runner.issue_cert`. The certbot DNS authenticator and its credentials
come from the *DNS* provider (`certbot_authenticator()` + `credential_env()`), so the
issuer is agnostic to which DNS vendor proves control. Ported from Atlas.
"""

from __future__ import annotations

import frappe
from frappe import _

from satellite.dns.base import DnsProvider
from satellite.tls import register
from satellite.tls.base import AuthResult, IssuedCert, TlsProvider
from satellite.tls.runner import issue_cert

LETS_ENCRYPT_PRODUCTION = "https://acme-v02.api.letsencrypt.org/directory"


@register
class LetsEncryptProvider(TlsProvider):
	provider_type = "Let's Encrypt"
	caa_issuer = "letsencrypt.org"

	def __init__(self) -> None:
		settings = frappe.get_single("Lets Encrypt Settings")
		self.acme_directory_url = settings.acme_directory_url or LETS_ENCRYPT_PRODUCTION
		self.account_email = settings.account_email

	def authenticate(self) -> AuthResult:
		if not self.account_email:
			return AuthResult(ok=False, error="Lets Encrypt Settings has no account_email")
		return AuthResult(ok=True, account_label=self.account_email)

	def issue(self, domain: str, dns_provider: DnsProvider) -> IssuedCert:
		# certbot is invoked with --agree-tos, so registering the ACME account agrees to
		# the ToS — there is no separate gate to check.
		if not self.account_email:
			frappe.throw(_("Lets Encrypt Settings: account_email is required"))
		return issue_cert(
			domain=domain,
			acme_directory_url=self.acme_directory_url,
			account_email=self.account_email,
			dns_authenticator=dns_provider.certbot_authenticator(),
			credential_env=dns_provider.credential_env(),
		)
