# Security policy

This is a self-hosted, single-user app. Please report vulnerabilities privately through
GitHub's **Report a vulnerability** button (Security tab → Advisories), not in a public issue.
Include steps to reproduce and the version (`server/__init__.py`). Only the latest release is supported.

Deployment guidance:

- Run it on your own machine or home network only. Do not expose port 8080 to the internet; it serves
  plain HTTP. Use a VPN or a reverse proxy with TLS if you need remote access, and set `LIA_COOKIE_SECURE=1`.
- The first account is created with a one-time setup code printed in the container log.
- Your data, app account and Claude login live in the `lia-data` Docker volume; back it up and protect it
  like any other financial record.
