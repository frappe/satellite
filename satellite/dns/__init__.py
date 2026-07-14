"""DNS provider registry. Vendors register their `DnsProvider` subclass via
`@register`; callers ask `for_dns_provider_type(type)` for an instance and never branch
on `provider_type`. The active vendor is a `Root Domain`'s denormalized
`dns_provider_type`. Ported from Atlas.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import frappe

# Re-exported for callers (the cert reconcile builds `dns.WildcardTargets`). base.py is
# stdlib-only at import time, so this triggers no boto3 load.
from satellite.dns.base import WildcardTargets

if TYPE_CHECKING:
	from satellite.dns.base import DnsProvider

__all__ = ["WildcardTargets", "for_dns_provider_type", "register"]


_REGISTRY: dict[str, type["DnsProvider"]] = {}


def register(cls: type["DnsProvider"]) -> type["DnsProvider"]:
	"""Class decorator that records `cls` against its `provider_type`."""
	_REGISTRY[cls.provider_type] = cls
	return cls


def for_dns_provider_type(provider_type: str) -> "DnsProvider":
	"""Return an instantiated `DnsProvider` for `provider_type`. Raises if none is
	registered."""
	_load_implementations()
	factory = _REGISTRY.get(provider_type)
	if factory is None:
		frappe.throw(f"No implementation for provider_type {provider_type!r}")
	return factory()


def _load_implementations() -> None:
	"""Import vendor modules so their `@register` decorators run. Idempotent."""
	import satellite.dns.route53  # noqa: F401
