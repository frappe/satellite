"""TLS issuer abstraction — produces the wildcard cert the proxy consumes.

A `TlsProvider` turns "(wildcard) domain + a DNS provider that can answer DNS-01" into
PEMs on the Satellite node's disk. Callers ask `for_tls_provider_type(type)` for an
instance and never branch on `provider_type`. Let's Encrypt is the only real
implementation; ZeroSSL / Self-Managed are registered stubs. Ported from Atlas.
"""

from __future__ import annotations

import dataclasses
from abc import ABC, abstractmethod
from typing import ClassVar

from satellite.dns.base import DnsProvider


@dataclasses.dataclass(frozen=True, slots=True)
class IssuedCert:
	"""What an issue/renew produced: on-disk PEM paths and the validity window parsed
	from the issued cert. `not_before`/`not_after` are the raw OpenSSL date strings the
	controller normalizes into the `TLS Certificate` Datetime fields."""

	fullchain_path: str
	privkey_path: str
	not_before: str
	not_after: str


@dataclasses.dataclass(frozen=True, slots=True)
class AuthResult:
	ok: bool
	account_label: str | None = None
	error: str | None = None


class TlsProvider(ABC):
	provider_type: ClassVar[str]

	# The CAA `issue` domain authorizing this issuer to mint certs. `None` means "no
	# public CA to authorize" (Self-Managed): omit the CAA record rather than emit a
	# meaningless one.
	caa_issuer: ClassVar[str | None] = None

	@abstractmethod
	def authenticate(self) -> AuthResult:
		"""Verify the issuer account is usable (ACME directory reachable / ToS agreed).
		Cheap, no issuance."""
		...

	@abstractmethod
	def issue(self, domain: str, dns_provider: DnsProvider) -> IssuedCert:
		"""Issue (or renew, idempotently) `*.<domain>`, proving control via
		`dns_provider`'s DNS-01 challenge. Returns the on-disk PEM paths + validity window.
		Runs on the Satellite node."""
		...
