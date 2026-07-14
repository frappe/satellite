"""DNS provider abstraction — the DNS-01 half of certificate issuance, plus the
wildcard record that points the regional domain at its proxy fleet.

A `DnsProvider` proves control of a zone to an ACME server via DNS-01. For the
challenge Satellite never writes TXT records itself; it hands certbot the provider's
plugin flag (`certbot_authenticator()`) and the vendor credentials as env
(`credential_env()`), and certbot's DNS plugin does the record dance. Satellite *does*
write the public `*.<domain>` A/AAAA records (`upsert_wildcard()`) so a client
resolving `<sub>.<domain>` reaches the proxy fleet. Ported verbatim from Atlas.
"""

from __future__ import annotations

import dataclasses
from abc import ABC, abstractmethod
from typing import ClassVar


@dataclasses.dataclass(frozen=True, slots=True)
class AuthResult:
	"""Outcome of a credential check."""

	ok: bool
	account_label: str | None = None
	error: str | None = None


@dataclasses.dataclass(frozen=True, slots=True)
class WildcardTargets:
	"""The proxy fleet's public addresses the regional wildcard should resolve to:
	`ipv4` (the proxies' public v4) and `ipv6` (the proxies' `/128`s). DNS round-robins
	over each list (spec/12-proxy.md)."""

	ipv4: list[str]
	ipv6: list[str]


class DnsProvider(ABC):
	provider_type: ClassVar[str]

	@abstractmethod
	def authenticate(self) -> AuthResult:
		"""Verify the credentials can reach the zone (Route 53: ListHostedZones). Backs
		Route53 Settings' Test Connection button."""
		...

	@abstractmethod
	def upsert_wildcard(self, domain: str, targets: WildcardTargets) -> list[str]:
		"""Publish `*.<domain>` A → `targets.ipv4` and AAAA → `targets.ipv6`, round-robin
		over the proxy fleet. Idempotent UPSERT. An empty family is skipped (never publish
		a wildcard pointing at nothing). Returns the record names written."""
		...

	@abstractmethod
	def credential_env(self) -> dict[str, str]:
		"""Vendor secrets as the environment certbot's DNS plugin reads (Route 53:
		`AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY`). Merged into the issue-cert
		subprocess env, never argv (secrets must not show up in `ps`)."""
		...

	@abstractmethod
	def certbot_authenticator(self) -> str:
		"""The certbot DNS authenticator NAME for this vendor (Route 53: `route53`). The
		runner turns it into the plugin flag (`--dns-route53`); the name (never a
		`--`-prefixed token) crosses the typed-CLI boundary, so argparse can't mistake a
		value for an option."""
		...
