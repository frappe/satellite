"""Self-service subdomain routing for guest-created bench sites (spec/18), now on the
Satellite side of the provisioner/orchestrator split.

A bench VM's owner spins up arbitrary sites from inside the guest; Satellite never ran
`bench new-site`, so no `Subdomain` row exists for those sites — yet they must become
routable through the regional proxy with no operator action. The design is **one-way
push**: the guest *tells* the controller what changed; the controller never reads the
guest back. Four whitelisted, guest-callable endpoints, each carrying **no
VM-identifying argument** — the controller resolves the calling VM from the request
source `/128` (*Caller resolution*) matched against the mirror's `guest_ipv6`:

  register(label)     BEFORE `bench new-site` → the authoritative INSERT that RESERVES
                                                the name (the real block-at-create gate).
  deregister(label)   AFTER `bench drop-site` / on rollback → DELETE the caller's own row.
  check_label(label)  OPTIONAL pre-flight    → read-only advisory availability answer.
  list()              ON DEMAND               → read-only; the caller VM's own rows.

Plus the custom-domain siblings (register/deregister_custom_domain, dns_records) and the
host-level queries (wildcard_domains, proxy_servers). Every call — read or write,
accepted or rejected — is recorded in the MyISAM `Bench Routing Audit` log
(audit-before-throw), so rejected / hijack-attempt rows survive the request rollback.

Differences from Atlas's `bench_routing.py`: uniqueness is the `Subdomain` unique key
(no `Site` table to consult); the region suffix comes from the active `Region Domain`;
and the CAA record in `dns_records` waits on Phase-5 TLS (omitted with a TODO).
"""

from __future__ import annotations

import json

import frappe
from frappe.rate_limiter import rate_limit

from satellite.routing.labels import (
	RESERVED_SUBDOMAINS,
	normalize,
	normalize_domain,
	validate_custom_domain,
	validate_label,
)
from satellite.routing.region import region_suffix
from satellite.satellite.doctype.subdomain_denylist.subdomain_denylist import is_denylisted
from satellite.services.routing import is_proxy, wildcard_targets

# The per-VM subdomain cap. Flat for now — Atlas keyed it on the VM's memory tier, which
# Satellite's mirror doesn't carry yet; when a `memory_megabytes` mirror field lands
# (a read-API addition), restore the tiers. The base Atlas tier was 20.
DEFAULT_SUBDOMAIN_CAP = 20


# ---------------------------------------------------------------------------
# register / deregister (the guest writes, the controller arbitrates)
# ---------------------------------------------------------------------------


@frappe.whitelist(allow_guest=True)
@rate_limit(limit=60, seconds=60)
def register(label: str) -> dict:
	"""The authoritative insert, run **before** `bench new-site`. Reserves the name —
	the real block-at-create gate, not `check_label`. Resolves the calling VM from the
	source `/128`, runs the shape → reserved+denylist → fleet-availability → per-VM cap
	rules in order, then inserts `Subdomain(subdomain=label, virtual_machine=<vm>,
	active=1)` whose `after_insert` reconciles the fleet:

	    {"status": "ok" | "taken" | "reserved" | "at_limit" | "invalid"}

	`ok` echoes `suffix` (the region domain) so the guest can build the FQDN without a
	second round-trip. A `DuplicateEntryError` (two benches racing the same label) maps
	to `taken` — the DB unique key is the atomic arbiter, and reserving FIRST is what
	makes the create un-blockable. Idempotent on an already-owned label. Carries NO
	`vm_uuid`; the row's `virtual_machine` is the source-resolved VM. Audited every path."""
	label = normalize(label)
	vm = _resolve_caller_vm("register", label)
	suffix = region_suffix()

	invalid = _label_invalid_reason(label)
	if invalid is not None:
		_audit("register", label, "invalid", business_reject=True, vm=vm.name)
		return {"status": "invalid", "reason": invalid}
	if _label_reserved(label):
		_audit("register", label, "reserved", business_reject=True, vm=vm.name)
		return {"status": "reserved"}

	# Idempotent on the caller's OWN row: a retried register for a label this VM already
	# owns is a clean ok (retry-after-transient). Checked before the fleet-availability
	# gate so an own-row retry never trips "taken" against itself.
	if frappe.db.exists("Subdomain", {"subdomain": label, "virtual_machine": vm.name}):
		_audit("register", label, "ok", business_reject=False, vm=vm.name)
		return {"status": "ok", "suffix": suffix}

	if frappe.db.exists("Subdomain", {"subdomain": label}):
		_audit("register", label, "taken", business_reject=True, vm=vm.name)
		return {"status": "taken"}
	if _subdomain_count(vm.name) >= cap_for_vm(vm):
		_audit("register", label, "at_limit", business_reject=True, vm=vm.name)
		return {"status": "at_limit"}

	# The atomic arbiter: the DB unique key. `subdomain` is BOTH the autoname source
	# (PRIMARY key) and unique:1, so a losing race can surface as either a
	# DuplicateEntryError or a UniqueValidationError — map both to `taken`.
	try:
		frappe.get_doc(
			{"doctype": "Subdomain", "subdomain": label, "virtual_machine": vm.name, "active": 1}
		).insert(ignore_permissions=True)
	except (frappe.DuplicateEntryError, frappe.UniqueValidationError):
		_audit("register", label, "taken", business_reject=True, vm=vm.name)
		return {"status": "taken"}
	_audit("register", label, "ok", business_reject=False, vm=vm.name)
	return {"status": "ok", "suffix": suffix}


@frappe.whitelist(allow_guest=True)
@rate_limit(limit=60, seconds=60)
def deregister(label: str) -> dict:
	"""The teardown signal, fired on **two** paths: after a deliberate `bench drop-site`,
	AND as the rollback when `bench new-site` fails after a successful `register`.
	Resolves the calling VM, finds its `Subdomain(subdomain=label, virtual_machine=<vm>)`,
	and deletes it — its `on_trash` deconverges the proxy:

	    {"status": "ok"}

	Scoped to the caller's OWN VM (a guest can never deregister another VM's route).
	Idempotent: an absent row is a clean `ok`. Audited."""
	label = normalize(label)
	vm = _resolve_caller_vm("deregister", label)
	name = frappe.db.get_value("Subdomain", {"subdomain": label, "virtual_machine": vm.name})
	if name:
		frappe.delete_doc("Subdomain", name, ignore_permissions=True)
	_audit("deregister", label, "ok", business_reject=False, vm=vm.name)
	return {"status": "ok"}


# ---------------------------------------------------------------------------
# check_label (the optional advisory pre-flight)
# ---------------------------------------------------------------------------


@frappe.whitelist(allow_guest=True)
@rate_limit(limit=60, seconds=60)
def check_label(label: str) -> dict:
	"""Read-only advisory availability answer, and **not the gate** — `register` is. An
	optional courtesy for early "that name's taken" feedback:

	    {"status": "ok" | "taken" | "reserved" | "at_limit" | "invalid",
	     "suffix": "<region domain>", "reason": "<message, invalid only>"}

	Runs the same checks `register` will and returns the region domain so the guest can
	build the FQDN. Advisory and fail-open by design — which is exactly *why* it can't be
	the gate; `register`'s atomic insert closes the window. Writes nothing but is audited."""
	vm = _resolve_caller_vm("check_label", normalize(label))
	suffix = region_suffix()
	label = normalize(label)

	invalid = _label_invalid_reason(label)
	if invalid is not None:
		_audit("check_label", label, "invalid", business_reject=True, vm=vm.name)
		return {"status": "invalid", "suffix": suffix, "reason": invalid}
	if _label_reserved(label):
		_audit("check_label", label, "reserved", business_reject=True, vm=vm.name)
		return {"status": "reserved", "suffix": suffix}
	if frappe.db.exists("Subdomain", {"subdomain": label}):
		_audit("check_label", label, "taken", business_reject=True, vm=vm.name)
		return {"status": "taken", "suffix": suffix}
	if _subdomain_count(vm.name) >= cap_for_vm(vm):
		_audit("check_label", label, "at_limit", business_reject=True, vm=vm.name)
		return {"status": "at_limit", "suffix": suffix}
	_audit("check_label", label, "ok", business_reject=False, vm=vm.name)
	return {"status": "ok", "suffix": suffix}


# ---------------------------------------------------------------------------
# list (the guest reads its OWN routes to find strays)
# ---------------------------------------------------------------------------


@frappe.whitelist(allow_guest=True)
@rate_limit(limit=60, seconds=60)
def list() -> dict:
	"""Read-only enumeration of the caller VM's OWN routes. Takes **no argument** — the VM
	is the source address, never a parameter:

	    {"domains": [{"label": "<label>", "fqdn": "<label>.<region domain>",
	                  "active": true | false}, ...]}

	`fqdn` is reconstructed controller-side (never echoed from a guest suffix). Writes
	nothing and does NOT touch the cap. A source matching no VM / a Terminated VM / a
	proxy is a clean reject. Audited.

	(Shadows the `list` builtin at module scope deliberately — the wire method must be
	`satellite.routing.api.list`. This module uses no `list(...)` builtin call.)"""
	vm = _resolve_caller_vm("list", "")
	suffix = region_suffix()
	rows = frappe.get_all("Subdomain", filters={"virtual_machine": vm.name}, fields=["subdomain", "active"])
	domains = [
		{"label": row["subdomain"], "fqdn": f"{row['subdomain']}.{suffix}", "active": bool(row["active"])}
		for row in rows
	]
	_audit("list", "", "ok", business_reject=False, vm=vm.name)
	return {"domains": domains}


# ---------------------------------------------------------------------------
# Host-level queries (wildcard-domains / proxy-servers)
# ---------------------------------------------------------------------------


@frappe.whitelist(allow_guest=True)
@rate_limit(limit=60, seconds=60)
def wildcard_domains() -> dict:
	"""The wildcard pattern(s) sites on this host may be named under:

	    {"domains": ["*.<active region domain>"]}

	Single-region today → exactly one pattern. Read-only, audited; no VM in scope, so the
	audit row carries a blank vm + the asking source `/128`."""
	suffix = region_suffix()
	_audit("wildcard_domains", "", "ok", business_reject=False, vm="")
	return {"domains": [f"*.{suffix}"]}


@frappe.whitelist(allow_guest=True)
@rate_limit(limit=60, seconds=60)
def proxy_servers() -> dict:
	"""The regional edge proxies' public IPs that front this host:

	    {"ips": [<v4 public IPs>, ..., <v6 /128s>, ...]}

	When the bench gets a non-empty list it locks nginx down to exactly these, trusts
	their `X-Forwarded-For`, and forwards it upstream untouched — closing the trust-root
	gap (caller resolution trusts a leftmost-XFF only an enforcing edge makes safe). The
	fleet is every Applied `routing-proxy` binding; the addresses are the same
	`wildcard_targets` the regional wildcard DNS resolves to. Read-only, audited."""
	ipv4, ipv6 = wildcard_targets()
	_audit("proxy_servers", "", "ok", business_reject=False, vm="")
	return {"ips": [*ipv4, *ipv6]}


# ---------------------------------------------------------------------------
# The custom-domain DNS recipe (the records the USER adds)
# ---------------------------------------------------------------------------


@frappe.whitelist(allow_guest=True)
@rate_limit(limit=60, seconds=60)
def dns_records(domain: str, site: str) -> dict:
	"""The DNS records the customer adds at THEIR provider so `domain` (a custom,
	non-wildcard name like `shop.acme.com`) reaches their Satellite site:

	    {"records": [
	        {"type": "CNAME", "name": "<domain>", "value": "<site regional FQDN>"},
	        {"type": "A",     "name": "<domain>", "value": "<proxy v4>"}, ...,
	        {"type": "AAAA",  "name": "<domain>", "value": "<proxy v6>"}, ...,
	    ]}

	Read-only and ADVISORY — Satellite writes nothing to any zone. Resolves the calling
	VM and verifies `site` is a regional FQDN this VM OWNS (a `Subdomain` row), so the
	CNAME target is a name reserved to this customer — the binding that stops another
	tenant claiming the route by pointing at the shared proxy. An unowned / off-wildcard
	`site` is a clean reject. Audited.

	TODO(phase-5): emit the CAA record once TLS providers move to Satellite — it needs the
	active issuer's public-CA identity, which Phase-5 brings."""
	vm = _resolve_caller_vm("dns_records", domain)
	suffix = f".{region_suffix()}"

	site = (site or "").strip().rstrip(".").lower()
	label = site[: -len(suffix)] if site.endswith(suffix) else None
	owned = label and frappe.db.exists("Subdomain", {"subdomain": label, "virtual_machine": vm.name})
	if not owned:
		_audit("dns_records", domain, "unowned_site", business_reject=True, vm=vm.name)
		frappe.throw(f"{site!r} is not a routable site this VM owns")

	ipv4, ipv6 = wildcard_targets()
	records = [{"type": "CNAME", "name": domain, "value": f"{label}{suffix}"}]
	records += [{"type": "A", "name": domain, "value": ip} for ip in ipv4]
	records += [{"type": "AAAA", "name": domain, "value": ip} for ip in ipv6]

	_audit("dns_records", domain, "ok", business_reject=False, vm=vm.name)
	return {"records": records}


# ---------------------------------------------------------------------------
# Custom-domain register / deregister (spec/18 Phase 2)
# ---------------------------------------------------------------------------


@frappe.whitelist(allow_guest=True)
@rate_limit(limit=60, seconds=60)
def register_custom_domain(domain: str) -> dict:
	"""Claim and provision an arbitrary external domain for the caller VM:

	    {"status": "ok" | "taken" | "invalid"}

	Resolves the calling VM, validates `domain` as a well-formed external FQDN NOT under
	the regional wildcard, checks fleet-wide uniqueness (the `Custom Domain` unique key is
	the atomic arbiter), and inserts `Custom Domain(domain, virtual_machine=<vm>,
	active=1)` whose `after_insert` reconciles the fleet's SNI + ACME maps — the route is
	live the moment the row exists. There is NO cert step on Satellite's side (SNI
	passthrough; the bench terminates TLS). Idempotent on the caller's OWN row. Audited."""
	domain = normalize_domain(domain)
	vm = _resolve_caller_vm("register_custom_domain", domain)
	region_domain = region_suffix()

	invalid = _custom_domain_invalid_reason(domain, region_domain)
	if invalid is not None:
		_audit("register_custom_domain", domain, "invalid", business_reject=True, vm=vm.name)
		return {"status": "invalid", "reason": invalid}

	if frappe.db.exists("Custom Domain", {"domain": domain, "virtual_machine": vm.name}):
		_audit("register_custom_domain", domain, "ok", business_reject=False, vm=vm.name)
		return {"status": "ok"}

	if frappe.db.exists("Custom Domain", domain):
		_audit("register_custom_domain", domain, "taken", business_reject=True, vm=vm.name)
		return {"status": "taken"}

	try:
		frappe.get_doc(
			{
				"doctype": "Custom Domain",
				"domain": domain,
				"virtual_machine": vm.name,
				"site": _caller_site_fqdn(vm.name, region_domain),
				"status": "Active",
				"active": 1,
			}
		).insert(ignore_permissions=True)
	except (frappe.DuplicateEntryError, frappe.UniqueValidationError):
		_audit("register_custom_domain", domain, "taken", business_reject=True, vm=vm.name)
		return {"status": "taken"}

	_audit("register_custom_domain", domain, "ok", business_reject=False, vm=vm.name)
	return {"status": "ok"}


@frappe.whitelist(allow_guest=True)
@rate_limit(limit=60, seconds=60)
def deregister_custom_domain(domain: str) -> dict:
	"""Tear down a custom-domain route, the full-FQDN twin of `deregister`:

	    {"status": "ok"}

	Resolves the calling VM, finds its `Custom Domain(domain, virtual_machine=<vm>)`, and
	deletes it — its `on_trash` deconverges the SNI map. Scoped to the caller's OWN VM.
	Idempotent. Audited."""
	domain = normalize_domain(domain)
	vm = _resolve_caller_vm("deregister_custom_domain", domain)
	name = frappe.db.get_value("Custom Domain", {"domain": domain, "virtual_machine": vm.name})
	if name:
		frappe.delete_doc("Custom Domain", name, ignore_permissions=True)
	_audit("deregister_custom_domain", domain, "ok", business_reject=False, vm=vm.name)
	return {"status": "ok"}


def _custom_domain_invalid_reason(domain: str, region_domain: str) -> str | None:
	"""The operator-facing message if `domain` fails the custom-domain shape rules, else
	None — returned as a typed `invalid` result, not an HTTP error, so the guest hook
	surfaces it verbatim."""
	try:
		validate_custom_domain(domain, region_domain)
	except frappe.ValidationError as exception:
		return str(exception)
	return None


def _caller_site_fqdn(vm_name: str, region_domain: str) -> str:
	"""The caller VM's own regional site FQDN, for the Custom Domain `site` provenance
	field. A VM may own several Subdomains; we record its first as the canonical site.
	Blank if the VM owns no Subdomain yet (`site` is provenance, not the routing target)."""
	label = frappe.db.get_value("Subdomain", {"virtual_machine": vm_name}, "subdomain")
	return f"{label}.{region_domain}" if label else ""


# ---------------------------------------------------------------------------
# Caller resolution (the VM is the source address, never a parameter)
# ---------------------------------------------------------------------------


def _resolve_caller_vm(endpoint: str, label: str):
	"""The VM whose packets reached the controller, resolved from the request's public
	IPv6 source `/128` (`frappe.local.request_ip`) matched against the mirror's
	`guest_ipv6` — NEVER from a request parameter. A guest is root in its own VM and can
	read any injected value, so a guest-supplied id could name another VM; the source
	address is the one VM-identifying fact the tenant cannot forge *if* read from a
	trusted edge that overwrites X-Forwarded-For (the hard prerequisite; the bench learns
	which edge to trust from `proxy_servers`).

	A spoofed/non-matching source, a Terminated VM, or a proxy is a clean reject:
	`frappe.throw` after an `unresolved` audit row carrying the source `/128` that tried.
	The reject rolls back the request transaction; the MyISAM audit row survives.

	`guest_ipv6` is not a unique column and can be recycled, so we FILTER OUT Terminated
	and proxy rows and FAIL CLOSED on ambiguity: if two live non-proxy VMs share a `/128`
	we resolve neither, rather than trusting an arbitrary first row."""
	source_ip = frappe.local.request_ip
	candidates = (
		frappe.get_all(
			"Virtual Machine",
			filters={"guest_ipv6": source_ip, "vm_status": ["!=", "Terminated"]},
			pluck="name",
			limit=3,
		)
		if source_ip
		else []
	)
	live = [name for name in candidates if not is_proxy(name)]
	if len(live) == 1:
		return frappe.get_doc("Virtual Machine", live[0])
	_audit(endpoint, label, "unresolved", business_reject=True, vm="")
	frappe.throw(f"No bench VM resolves from the request source address {source_ip!r}")


# ---------------------------------------------------------------------------
# The per-VM subdomain cap
# ---------------------------------------------------------------------------


def cap_for_vm(vm) -> int:
	"""The per-VM subdomain ceiling. Flat `DEFAULT_SUBDOMAIN_CAP` today — restore Atlas's
	memory tiers once the mirror carries `memory_megabytes`."""
	return DEFAULT_SUBDOMAIN_CAP


def _subdomain_count(vm_name: str) -> int:
	"""How many `Subdomain` rows this VM owns — the cap counts a *write*, so every row
	consumes a slot. `register` always inserts and `deregister` deletes, so a row is
	either present or gone."""
	return frappe.db.count("Subdomain", {"virtual_machine": vm_name})


# ---------------------------------------------------------------------------
# The brand/keyword denylist seam (shared by register + check_label)
# ---------------------------------------------------------------------------


def _label_invalid_reason(label: str) -> str | None:
	"""The operator-facing message if `label` fails the shape rules, else None. A typed
	`invalid` result, not an HTTP error, so the guest hook surfaces it verbatim."""
	try:
		validate_label(label)
	except frappe.ValidationError as exception:
		return str(exception)
	return None


def _label_reserved(label: str) -> bool:
	"""True if `label` is blocked: the frozen structural set (`RESERVED_SUBDOMAINS`) OR
	the live brand denylist. One seam both `register` and `check_label` call, so they
	reject the same labels in the same order."""
	return normalize(label).lower() in RESERVED_SUBDOMAINS or is_denylisted(label)


# ---------------------------------------------------------------------------
# The request audit log (MyISAM, append-only, sole writer)
# ---------------------------------------------------------------------------


def _audit(endpoint: str, label: str, status: str, *, business_reject: bool, vm: str) -> None:
	"""Write one `Bench Routing Audit` row, on EVERY path of EVERY endpoint including the
	reject/throw paths (audit-before-throw). Records both `source_ip` (the value caller
	resolution acted on) and `fwd_headers` (the whole forwarded chain verbatim); their
	divergence is the leftmost-XFF forgery signal.

	Persistence rides MyISAM's per-statement auto-commit ALONE — no `frappe.db.commit()`,
	which would flush partial transactional work before a later throw. `vm` is a Data
	SNAPSHOT, not a Link (an audit row must outlive the VM). Audit failure must never
	break the endpoint: the log is forensic, so a write error is logged and swallowed."""
	try:
		frappe.get_doc(
			{
				"doctype": "Bench Routing Audit",
				"endpoint": endpoint,
				"label": label or "",
				"status": status,
				"business_reject": 1 if business_reject else 0,
				"vm": vm or "",
				"source_ip": frappe.local.request_ip or "",
				"fwd_headers": _forwarded_headers(),
				"request_body": _request_body(),
			}
		).insert(ignore_permissions=True)
	except Exception as exception:
		frappe.log_error(f"Bench routing audit insert failed: {exception}", "Bench routing audit")


def _forwarded_headers() -> str:
	"""The forwarded-header chain (incl. the raw X-Forwarded-For) verbatim — the
	guest-controlled bytes whose divergence from `source_ip` is the hijack signal. Empty
	outside a request context (a unit harness call)."""
	if not getattr(frappe.local, "request", None):
		return ""
	wanted = ("X-Forwarded-For", "X-Forwarded-Host", "X-Forwarded-Proto", "X-Real-IP", "Forwarded")
	chain = {name: frappe.get_request_header(name) for name in wanted if frappe.get_request_header(name)}
	return json.dumps(chain) if chain else ""


def _request_body() -> str:
	"""The raw POST body verbatim (guest-controlled). Empty outside a request context."""
	request = getattr(frappe.local, "request", None)
	if request is None:
		return ""
	try:
		return request.get_data(as_text=True) or ""
	except Exception:
		return ""
