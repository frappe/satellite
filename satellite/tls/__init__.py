"""TLS issuance on the Satellite: providers that turn a wildcard domain into PEMs on
the Satellite node's disk, then push them to the proxy fleet.

Unlike Atlas (which ran certbot as a controller-local Task), the Satellite runs certbot
as a plain local subprocess (`tls.runner`) — the PEMs land here, then `services.proxy.
push_cert` ships them to each proxy over run_guest. Ported from Atlas's `tls/` tree.
"""
