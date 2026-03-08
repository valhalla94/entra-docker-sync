# entra-docker-sync

A bash-driven Python tool that provisions Docker containers on Linux hosts based on Microsoft Entra ID (formerly Azure AD) group memberships. It polls the Microsoft Graph API, maps group memberships to container lifecycle actions, tracks state via Terraform-compatible state files, and generates lifecycle event reports.

---

## Table of Contents

- [Overview](#overview)
- [Architecture](#architecture)
- [Prerequisites](#prerequisites)
- [Installation](#installation)
- [Configuration](#configuration)
- [Usage](#usage)
- [State Management](#state-management)
- [Reporting](#reporting)
- [Security Considerations](#security-considerations)
- [Troubleshooting](#troubleshooting)
- [Contributing](#contributing)
- [License](#license)

---

## Overview

`entra-docker-sync` automates the lifecycle of Docker containers based on Entra ID group membership. When a Windows user is added to a designated Entra ID group, a corresponding Docker container is started on the configured Linux host. When the user is removed from the group, the container is stopped and removed.

This is useful for:
- Ephemeral developer environments tied to AD group access
- Automated container provisioning for onboarding/offboarding workflows
- Audit-friendly infrastructure tied to identity management

---

## Architecture

```
+-------------------------+       +----------------------+
|   Microsoft Entra ID    |       |   Linux Docker Host  |
|   (Graph API)           |       |                      |
|                         |       |  +-----------------+ |
|  Group A --> User 1     +------>+  | Container: u1   | |
|  Group A --> User 2     |       |  | Container: u2   | |
|  Group B --> User 3     |       |  | Container: u3   | |
+-------------------------+       |  +-----------------+ |
                                  |                      |
         +------------------------+  Terraform State     |
         |  entra-docker-sync     |  (tfstate files)     |
         |  main.py               |                      |
         |   auth.py              |  Log Reports         |
         |   graph_api.py         |  (lifecycle events)  |
         |   docker_manager.py    |                      |
         |   state_manager.py     |                      |
         |   report_generator.py  |                      |
         +------------------------+----------------------+
```

### Module Breakdown

| Module | Responsibility |
|---|---|
| `auth.py` | Authenticates with Microsoft Graph API using client credentials flow |
| `graph_api.py` | Polls Entra ID groups and retrieves member user details |
| `docker_manager.py` | Starts, stops, and removes Docker containers on the local host |
| `state_manager.py` | Reads and writes Terraform-compatible state files to track provisioned containers |
| `report_generator.py` | Generates log reports of container lifecycle events tied to Entra ID identities |
| `main.py` | Orchestrates the full sync cycle |

---

## Prerequisites

- Python 3.9+
- Docker Engine installed and accessible to the running user (or root)
- A registered Microsoft Entra ID (Azure AD) application with the following Graph API permissions:
  - `Group.Read.All`
  - `User.Read.All`
- Client credentials (tenant ID, client ID, client secret)
- Linux host with outbound HTTPS access to `graph.microsoft.com`

---

## Installation

### 1. Clone the repository

```bash
git clone https://github.com/your-org/entra-docker-sync.git
cd entra-docker-sync
```

### 2. Create and activate a virtual environment

```bash
python3 -m venv .venv
source .venv/bin/activate
```

### 3. Install the package

```bash
pip install -e .
```

Or install dependencies directly:

```bash
pip install -r requirements.txt
```

---

## Configuration

All configuration is managed via `config/settings.yaml`. Copy and edit the example:

```bash
cp config/settings.yaml.example config/settings.yaml
```

### Full Configuration Reference

```yaml
# config/settings.yaml

entra:
  # Azure AD / Entra ID tenant identifier
  tenant_id: "your-tenant-id"

  # Application (client) ID of your registered Entra app
  client_id: "your-client-id"

  # Client secret value (consider using an environment variable override instead)
  client_secret: "your-client-secret"

  # List of Entra ID group object IDs to monitor
  # Each group maps to a Docker image that will be provisioned for its members
  groups:
    - group_id: "xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
      docker_image: "ubuntu:22.04"
      container_prefix: "dev"
    - group_id: "yyyyyyyy-yyyy-yyyy-yyyy-yyyyyyyyyyyy"
      docker_image: "python:3.11-slim"
      container_prefix: "py"

docker:
  # Docker socket path (default: /var/run/docker.sock)
  socket: "/var/run/docker.sock"

  # Default resource limits applied to all provisioned containers
  default_memory_limit: "512m"
  default_cpu_shares: 512

  # Network to attach provisioned containers to
  network: "bridge"

state:
  # Directory where Terraform-compatible state files are stored
  state_dir: "/var/lib/entra-docker-sync/state"

  # State file naming convention: {state_dir}/{group_id}.tfstate
  # This directory must be writable by the process user

reporting:
  # Directory where lifecycle event log reports are written
  report_dir: "/var/log/entra-docker-sync"

  # Log level: DEBUG, INFO, WARNING, ERROR
  log_level: "INFO"

  # Retain reports for this many days before pruning
  retention_days: 30

sync:
  # How often (in seconds) to poll Entra ID for group membership changes
  poll_interval: 300

  # If true, dry-run mode logs intended actions without executing them
  dry_run: false
```

### Environment Variable Overrides

Sensitive values can be set via environment variables to avoid storing secrets in YAML:

| Variable | Overrides |
|---|---|
| `ENTRA_TENANT_ID` | `entra.tenant_id` |
| `ENTRA_CLIENT_ID` | `entra.client_id` |
| `ENTRA_CLIENT_SECRET` | `entra.client_secret` |
| `STATE_DIR` | `state.state_dir` |
| `REPORT_DIR` | `reporting.report_dir` |

Example:

```bash
export ENTRA_TENANT_ID="your-tenant-id"
export ENTRA_CLIENT_ID="your-client-id"
export ENTRA_CLIENT_SECRET="your-client-secret"
```

---

## Usage

### Run a single sync cycle

```bash
python -m entra_docker_sync.main --config config/settings.yaml
```

### Run in continuous polling mode

```bash
python -m entra_docker_sync.main --config config/settings.yaml --daemon
```

In daemon mode the process polls Entra ID every `sync.poll_interval` seconds (default: 300).

### Dry-run mode

Log what would happen without making any Docker or state changes:

```bash
python -m entra_docker_sync.main --config config/settings.yaml --dry-run
```

### Run as a systemd service

Create `/etc/systemd/system/entra-docker-sync.service`:

```ini
[Unit]
Description=Entra Docker Sync
After=network.target docker.service
Requires=docker.service

[Service]
Type=simple
User=entra-sync
EnvironmentFile=/etc/entra-docker-sync/env
ExecStart=/opt/entra-docker-sync/.venv/bin/python -m entra_docker_sync.main \
    --config /etc/entra-docker-sync/settings.yaml \
    --daemon
Restart=on-failure
RestartSec=30

[Install]
WantedBy=multi-user.target
```

Enable and start:

```bash
systemctl daemon-reload
systemctl enable --now entra-docker-sync
```

---

## State Management

State is stored as Terraform-compatible `.tfstate` JSON files, one per monitored Entra ID group:

```
/var/lib/entra-docker-sync/state/
  xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx.tfstate
  yyyyyyyy-yyyy-yyyy-yyyy-yyyyyyyyyyyy.tfstate
```

Each state file tracks which containers were provisioned for which user identities. On the next sync cycle the current Entra ID membership is diffed against the state:

- **User added to group** → container is started, state is updated
- **User removed from group** → container is stopped and removed, state is updated
- **No change** → no action taken

State files can be inspected directly:

```bash
cat /var/lib/entra-docker-sync/state/<group-id>.tfstate | python3 -m json.tool
```

---

## Reporting

Lifecycle events are written to dated log files in the configured report directory:

```
/var/log/entra-docker-sync/
  sync-2024-01-15.log
  sync-2024-01-16.log
```

Each log entry includes:
- Timestamp (UTC)
- Entra user UPN (e.g., `jsmith@contoso.com`)
- Entra group ID
- Action (`CONTAINER_STARTED`, `CONTAINER_STOPPED`, `CONTAINER_REMOVED`, `NO_CHANGE`)
- Container name and ID
- Docker image used

Example log output:

```
2024-01-15T09:02:11Z INFO  [CONTAINER_STARTED] user=jsmith@contoso.com group=xxxxxxxx container=dev-jsmith image=ubuntu:22.04 id=a1b2c3d4e5f6
2024-01-15T09:02:13Z INFO  [CONTAINER_STARTED] user=adoe@contoso.com group=xxxxxxxx container=dev-adoe image=ubuntu:22.04 id=b2c3d4e5f6a1
2024-01-15T09:05:44Z INFO  [CONTAINER_STOPPED] user=bwilliams@contoso.com group=yyyyyyyy container=py-bwilliams image=python:3.11-slim id=c3d4e5f6a1b2
```

To tail live events:

```bash
tail -f /var/log/entra-docker-sync/sync-$(date +%Y-%m-%d).log
```

---

## Security Considerations

1. **Client secrets**: Never commit `settings.yaml` with real credentials. Use environment variables or a secrets manager (e.g., HashiCorp Vault, AWS Secrets Manager).

2. **Least privilege**: Grant the Entra application only `Group.Read.All` and `User.Read.All`. It does not need write access to Entra ID.

3. **Docker socket access**: The process user needs access to `/var/run/docker.sock`. Use a dedicated service account and add it to the `docker` group rather than running as root.

4. **State file permissions**: State files contain container IDs and user UPNs. Restrict access:
   ```bash
   chmod 700 /var/lib/entra-docker-sync/state
   ```

5. **Network egress**: Only outbound HTTPS (port 443) to `login.microsoftonline.com` and `graph.microsoft.com` is required.

---

## Troubleshooting

### Authentication errors

```
ERROR Failed to acquire token: AADSTS700016
```

Verify `tenant_id` and `client_id` are correct and the application registration exists in the correct tenant.

### Graph API permission errors

```
ERROR 403 Forbidden: Insufficient privileges
```

Ensure the Entra application has `Group.Read.All` and `User.Read.All` granted as **application permissions** (not delegated) with admin consent.

### Docker connection errors

```
ERROR Cannot connect to Docker daemon at unix:///var/run/docker.sock
```

Verify Docker is running and the process user is in the `docker` group:

```bash
systemctl status docker
groups $USER
```

### State file corruption

If a state file is corrupted, remove it and let the next sync cycle rebuild state from live Docker containers:

```bash
rm /var/lib/entra-docker-sync/state/<group-id>.tfstate
```

---

## Contributing

1. Fork the repository
2. Create a feature branch: `git checkout -b feat/your-feature`
3. Commit your changes using conventional commits
4. Open a pull request

Please ensure all new code includes appropriate logging and handles API/Docker errors gracefully.

---

## License

MIT License. See [LICENSE](LICENSE) for details.
