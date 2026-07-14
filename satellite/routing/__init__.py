"""Guest-plane routing: the control plane for bench-site subdomains, custom
domains, and TCP port mappings, and the desired maps the proxy fleet serves.

Ported from Atlas (spec/18 self-serve routing, spec/12 proxy, spec/17 tcp-proxy) as
the first clean guest-plane move of the provisioner/orchestrator split (spec/28): a
route terminates INSIDE a VM, so it belongs to Satellite. Atlas keeps only base
networking and the VM's existence; Satellite owns the subdomain table, arbitrates the
guest-callable API, and reconciles the edge proxies over its own SSH.

Modules:
  region   — the active Region Domain (the wildcard suffix FQDNs are built under).
  labels   — subdomain + custom-domain shape rules (the pure validators).
  desired  — the canonical subdomain / custom-domain maps the proxy serves.
  ports    — the TCP port pool, allocation, and the desired port map.
  api      — the guest-callable endpoints (register/deregister/...), arbitrated
             controller-side and resolved by the caller's source /128.
"""
