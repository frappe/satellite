"""Site service — install a Frappe site into a VM's baked bench (spec/14, spec/28).

The guest-plane service installation: binding `site` to a VM deploys the site into its
golden bench (rename the baked site to the FQDN, regenerate the vhost, confirm serving).
The per-site inputs ride the binding `config` — the FQDN Satellite owns (routing) plus
the opaque Central handoff params (central_endpoint/auth_token, admin mode/domain) it
forwards into the guest without interpreting. Central never calls here: the binding is
created off the mirrored VM, and the deployed site phones Central back over its baked-in
central_endpoint.
"""

from __future__ import annotations

import json

import frappe

from satellite import registration
from satellite.bench import deploy_site, readiness_path_for_mode, wait_for_http


@frappe.whitelist()
def deploy(
	atlas: str,
	remote_id: str,
	fqdn: str,
	central_endpoint: str | None = None,
	central_auth_token: str | None = None,
	mode: str | None = None,
	admin_domain: str | None = None,
) -> dict:
	"""Central-facing entrypoint: request a site install on a VM. Authenticated like the
	rest of the boundary (System Manager — Central holds a Satellite API key). Returns
	immediately: a deploy takes minutes (rename + readiness), so the actual work is
	enqueued and Central polls the `site` binding's status (or the deployed site phones
	Central back over its baked-in central_endpoint). Idempotent per (atlas, VM)."""
	frappe.only_for("System Manager")
	frappe.enqueue(
		"satellite.services.site.run_deploy",
		queue="long",
		timeout=1800,
		atlas=atlas,
		remote_id=remote_id,
		fqdn=fqdn,
		central_endpoint=central_endpoint,
		central_auth_token=central_auth_token,
		mode=mode,
		admin_domain=admin_domain,
	)
	return {"status": "accepted", "atlas": atlas, "remote_id": remote_id, "fqdn": fqdn}


def run_deploy(atlas: str, remote_id: str, fqdn: str, **params) -> str:
	"""Background job: mirror the VM on demand (so Satellite need not have seen it yet),
	then (re)create the `site` binding whose apply runs the deploy. Returns the binding
	name. A re-deploy replaces the prior binding so a changed config takes effect."""
	vm_name = registration.register_vm(atlas, remote_id)
	config = {"fqdn": fqdn, **{key: value for key, value in params.items() if value}}
	existing = frappe.db.exists("Service Binding", {"virtual_machine": vm_name, "service": "site"})
	if existing:
		frappe.delete_doc("Service Binding", existing, force=1, ignore_permissions=True)
	binding = frappe.get_doc(
		{
			"doctype": "Service Binding",
			"virtual_machine": vm_name,
			"service": "site",
			"config": json.dumps(config),
		}
	).insert(ignore_permissions=True)
	return binding.name


class SiteService:
	def apply(self, vm, binding) -> None:
		"""Deploy the site into the guest, then block on the HTTP readiness gate
		(Contract B) so the binding only reports Applied once Frappe actually serves the
		FQDN."""
		config = json.loads(binding.config) if binding.config else {}
		fqdn = config.get("fqdn")
		if not fqdn:
			frappe.throw("site binding config needs an 'fqdn'")
		deploy_site(
			vm.name,
			fqdn,
			central_endpoint=config.get("central_endpoint"),
			central_auth_token=config.get("central_auth_token"),
			mode=config.get("mode"),
			admin_domain=config.get("admin_domain"),
		)
		mode = config.get("mode") or vm.build_mode
		wait_for_http(vm.guest_ipv6, fqdn, path=readiness_path_for_mode(mode))

	def withdraw(self, vm, binding) -> None:
		"""No-op: the baked site is the VM's own identity, so unbinding the service does
		not drop it — that is VM termination (deregister removes the mirror). Kept for the
		handler contract; idempotent."""
