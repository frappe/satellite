"""TLS issuance on the Satellite: providers that turn a wildcard domain into PEMs on
the Satellite node's disk, then push them to the proxy fleet.

Unlike Atlas (which ran certbot as a controller-local Task), the Satellite runs certbot
as a plain local subprocess (`tls.runner`) — the PEMs land here, then `services.proxy.
push_cert` ships them to each proxy over run_guest.

This module is the provider registry: issuers register their `TlsProvider` subclass via
`@register`; callers ask `for_tls_provider_type(type)` for an instance and never branch
on `provider_type`. The active issuer is a `Root Domain`'s denormalized
`tls_provider_type`. Ported from Atlas's `tls/` tree.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import frappe

if TYPE_CHECKING:
	from satellite.tls.base import TlsProvider


_REGISTRY: dict[str, type["TlsProvider"]] = {}


def register(cls: type["TlsProvider"]) -> type["TlsProvider"]:
	"""Class decorator that records `cls` against its `provider_type`."""
	_REGISTRY[cls.provider_type] = cls
	return cls


def for_tls_provider_type(provider_type: str) -> "TlsProvider":
	"""Return an instantiated `TlsProvider` for `provider_type`. Raises if none is
	registered."""
	_load_implementations()
	factory = _REGISTRY.get(provider_type)
	if factory is None:
		frappe.throw(f"No implementation for provider_type {provider_type!r}")
	return factory()


def _load_implementations() -> None:
	"""Import issuer modules so their `@register` decorators run. Idempotent."""
	import satellite.tls.letsencrypt  # noqa: F401
	import satellite.tls.self_managed  # noqa: F401
	import satellite.tls.zerossl  # noqa: F401
