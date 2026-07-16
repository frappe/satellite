"""TLS service — issue the regional wildcard cert and push it to the proxy fleet.

The region-level flow that ties the seams together: for an active Region Domain, publish
the wildcard DNS at the proxy fleet, issue (or renew) `*.<domain>` via the region's TLS
provider, record a `TLS Certificate`, and push the PEMs to every proxy over run_guest.
A daily `renew_expiring` sweep re-issues before expiry (certbot is idempotent). TLS is a
region-level concern, not a per-VM binding, so there is no Service handler — the
scheduler + a manual `issue_and_push` drive it.
"""

from __future__ import annotations

import frappe
from frappe.utils import add_to_date, get_datetime, now_datetime

from satellite.dns import WildcardTargets, for_dns_provider_type
from satellite.services.proxy import push_cert
from satellite.services.routing import _proxy_vms, wildcard_targets
from satellite.tls import for_tls_provider_type

# Re-issue when the cert expires within this window (certbot renews-or-skips a still-valid
# lineage, so a daily sweep is cheap).
RENEW_WINDOW_DAYS = 30


def issue_and_push(region_domain: str) -> str:
	"""Issue (or renew) `*.<domain>` for a Region Domain and push it to every proxy.
	Publishes the wildcard DNS first, records a `TLS Certificate`, then pushes. Returns
	the cert name."""
	doc = frappe.get_doc("Region Domain", region_domain)
	dns = for_dns_provider_type(doc.dns_provider_type)
	tls = for_tls_provider_type(doc.tls_provider_type)

	ipv4, ipv6 = wildcard_targets()
	dns.upsert_wildcard(doc.domain, WildcardTargets(ipv4=ipv4, ipv6=ipv6))
	issued = tls.issue(doc.domain, dns)

	cert = _record_certificate(doc.domain, issued)
	push_to_proxies(issued.fullchain_path, issued.privkey_path)
	return cert


def push_to_proxies(fullchain_path: str, privkey_path: str) -> list[str]:
	"""Read the PEMs off the Satellite node's disk and push them to every proxy in the
	fleet via `push_cert`. Returns the proxy VMs pushed to."""
	fullchain = _read_pem(fullchain_path)
	privkey = _read_pem(privkey_path)
	pushed = []
	for vm in _proxy_vms():
		push_cert(vm, fullchain, privkey)
		pushed.append(vm)
	return pushed


def renew_expiring() -> list[str]:
	"""Daily sweep: (re)issue the wildcard cert for each active Region Domain whose cert
	is missing or within the renewal window. A per-domain failure is logged and marked,
	never aborts the sweep. Returns the domains renewed."""
	renewed = []
	for name in frappe.get_all("Region Domain", filters={"is_active": 1}, pluck="name"):
		domain = frappe.db.get_value("Region Domain", name, "domain")
		if not _needs_renewal(domain):
			continue
		try:
			issue_and_push(name)
			renewed.append(domain)
		except Exception as exception:
			frappe.log_error(f"TLS renew failed for {domain}: {exception}", "TLS renew")
			_mark_failed(domain, str(exception))
	return renewed


def _naive(value):
	"""certbot's openssl validity dates carry a GMT tzinfo (`… 2026 GMT`), which
	`get_datetime` turns into a tz-AWARE datetime that MariaDB's naive DATETIME column
	rejects. The value is already UTC, so drop the offset before persisting."""
	dt = get_datetime(value)
	return dt.replace(tzinfo=None) if dt and dt.tzinfo else dt


def _record_certificate(domain: str, issued) -> str:
	values = {
		"fullchain_path": issued.fullchain_path,
		"privkey_path": issued.privkey_path,
		"not_before": _naive(issued.not_before),
		"not_after": _naive(issued.not_after),
		"status": "Active",
		"last_error": None,
	}
	name = frappe.db.exists("TLS Certificate", domain)
	if name:
		doc = frappe.get_doc("TLS Certificate", name)
		doc.update(values)
		doc.save(ignore_permissions=True)
	else:
		doc = frappe.get_doc({"doctype": "TLS Certificate", "domain": domain, **values}).insert(
			ignore_permissions=True
		)
	return doc.name


def _needs_renewal(domain: str) -> bool:
	"""True if `domain` has no cert or its cert expires within the renewal window."""
	not_after = frappe.db.get_value("TLS Certificate", domain, "not_after")
	if not not_after:
		return True
	return get_datetime(not_after) <= add_to_date(now_datetime(), days=RENEW_WINDOW_DAYS)


def _mark_failed(domain: str, error: str) -> None:
	if frappe.db.exists("TLS Certificate", domain):
		frappe.db.set_value("TLS Certificate", domain, {"status": "Failed", "last_error": error[:500]})


def _read_pem(path: str) -> str:
	with open(path) as handle:
		return handle.read()
