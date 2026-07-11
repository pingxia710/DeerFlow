# Security Policy

## Supported Versions

As deer-flow doesn't provide an official release yet, please use the latest version for the security updates.
Currently, we have two branches to maintain:
* main branch for deer-flow 2.x
* main-1.x branch for deer-flow 1.x 

## Secure-by-default Docker settings

The Docker/nginx development stack is local-first by default:

- nginx binds to `127.0.0.1` by default. Set `DEER_FLOW_BIND_HOST=0.0.0.0` only when you intentionally expose DeerFlow outside the host.
- When the bundled nginx runs behind another reverse proxy, set `DEER_FLOW_TRUSTED_OUTER_PROXIES` to the comma-separated CIDRs of only those directly trusted proxy hops. It is empty by default, so arbitrary `X-Forwarded-For` headers never affect client-IP login throttling. The outer proxy must append or overwrite `X-Forwarded-For`; never add client networks to this allowlist.
- API docs are disabled by default. Set `GATEWAY_ENABLE_DOCS=true` to enable the Gateway docs endpoints, and set `DEER_FLOW_EXPOSE_API_DOCS=true` only when `/docs`, `/redoc`, or `/openapi.json` should also be reachable through Docker/nginx.
- The sandbox provisioner API is internal-only and is never proxied by the external nginx entrypoint.
- Do not directly publish the provisioner service on port `8002`; it is intended for Docker-internal backend access. The nginx gate protects only the external entrypoint, so do not attach untrusted containers to the Docker internal network.

## Reporting a Vulnerability

Please go to https://github.com/bytedance/deer-flow/security to report the vulnerability you find.
