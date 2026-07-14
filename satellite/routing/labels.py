"""Subdomain + custom-domain shape rules — the pure label validators.

Ported from Atlas's `subdomain_label` + `custom_domain_label` (spec/14, spec/18). A
`Subdomain` is a single DNS label under the one regional wildcard (`app` →
`app.<region>`); a `Custom Domain` is an arbitrary external host the customer already
owns (`shop.acme.com`). The rules are deliberately separate — the dot ban + per-VM cap
are correct for wildcard labels and must not loosen on the custom-domain path.

The reserved-name set and the brand denylist (`subdomain_denylist.is_denylisted`) are
the two Component-H gates `register`/`check_label` apply on top of these shape rules.
No Site/Pilot coupling here (that lived in Atlas's `is_taken` against the `Site`
table); Satellite's authoritative uniqueness is the `Subdomain` unique key itself.
"""

from __future__ import annotations

import frappe
from frappe import _

# A subdomain label that is not the user's to take — the operational names a fleet
# reserves. Frozen (Contract A, spec/14); everything else is arbitrated by the
# `Subdomain` unique key at insert.
RESERVED_SUBDOMAINS = frozenset(
	{
		"www",
		"admin",
		"api",
		"proxy",
		"app",
		"dashboard",
		"mail",
		"ns",
		"root",
	}
)

# DNS label rules: 1-63 chars, lowercase alphanumerics and hyphens, no leading or
# trailing hyphen. The dot ban is enforced separately so the message is clear (a dot
# would escape the one regional wildcard the proxy terminates).
LABEL_MAX_LENGTH = 63
DOMAIN_MAX_LENGTH = 253


# ---------------------------------------------------------------------------
# Subdomain labels (the wildcard path)
# ---------------------------------------------------------------------------


def normalize(subdomain: str | None) -> str:
	"""The canonical label: stripped, as the user typed it (case is *validated*, not
	silently lowered, so `Acme` fails loud rather than quietly becoming `acme`)."""
	return (subdomain or "").strip()


def validate_label(subdomain: str | None) -> None:
	"""Single DNS label, no dots. A dot would escape the regional wildcard and need its
	own cert (deferred). Enforce lowercase `[a-z0-9-]`, no leading/trailing hyphen,
	length cap. Throws a clear, field-specific message."""
	label = normalize(subdomain)
	if not label:
		frappe.throw(_("A subdomain is required"))
	if "." in label:
		frappe.throw(_("Subdomain must be a single label with no dots"))
	if label != label.lower():
		frappe.throw(_("Subdomain must be lowercase"))
	if len(label) > LABEL_MAX_LENGTH:
		frappe.throw(f"Subdomain must be at most {LABEL_MAX_LENGTH} characters")
	if label.startswith("-") or label.endswith("-"):
		frappe.throw(_("Subdomain must not start or end with a hyphen"))
	if not all((c.isascii() and c.isalnum()) or c == "-" for c in label):
		frappe.throw(_("Subdomain may only contain lowercase letters, digits, and hyphens"))


def validate_reserved(subdomain: str | None) -> None:
	if normalize(subdomain).lower() in RESERVED_SUBDOMAINS:
		frappe.throw(f"Subdomain '{normalize(subdomain)}' is reserved — choose another")


# ---------------------------------------------------------------------------
# Custom domains (the full-FQDN path)
# ---------------------------------------------------------------------------


def normalize_domain(domain: str | None) -> str:
	"""The canonical custom domain: stripped, lowercased, trailing dot removed.

	Hostnames are case-insensitive and a FQDN may carry a trailing root dot
	(`shop.acme.com.`); both are normalized away so the routing key is canonical
	(unlike `normalize`, which validates case loudly — an external hostname the
	customer pastes from their DNS provider is conventionally case-insensitive)."""
	return (domain or "").strip().lower().rstrip(".")


def validate_custom_domain(domain: str | None, region_domain: str) -> None:
	"""Raise unless `domain` is a well-formed external FQDN routable as a Custom Domain.

	`region_domain` is the active regional wildcard suffix (e.g. `blr1.frappe.dev`); a
	name under it is rejected (it belongs in the `register(label)` wildcard path).
	Throws a clear, field-specific message the guest surfaces verbatim."""
	name = normalize_domain(domain)
	if not name:
		frappe.throw(_("A domain is required"))
	if "." not in name:
		frappe.throw(
			_("A custom domain must be a full domain name (e.g. shop.example.com), not a bare label")
		)
	if len(name) > DOMAIN_MAX_LENGTH:
		frappe.throw(f"Domain must be at most {DOMAIN_MAX_LENGTH} characters")

	# A name under our regional wildcard is a Subdomain, not a Custom Domain.
	region_domain = (region_domain or "").strip().lower().rstrip(".")
	if region_domain and (name == region_domain or name.endswith(f".{region_domain}")):
		frappe.throw(
			f"{name!r} is under the regional wildcard {region_domain!r}; "
			"register it as a subdomain, not a custom domain"
		)

	for label in name.split("."):
		_validate_domain_label(label, name)


def _validate_domain_label(label: str, domain: str) -> None:
	if not label:
		frappe.throw(f"{domain!r} has an empty label (a doubled or leading/trailing dot)")
	if len(label) > LABEL_MAX_LENGTH:
		frappe.throw(f"Label {label!r} in {domain!r} exceeds {LABEL_MAX_LENGTH} characters")
	if label.startswith("-") or label.endswith("-"):
		frappe.throw(f"Label {label!r} in {domain!r} must not start or end with a hyphen")
	if not all((c.isascii() and c.isalnum()) or c == "-" for c in label):
		frappe.throw(
			f"Label {label!r} in {domain!r} may only contain lowercase letters, digits, and hyphens"
		)
