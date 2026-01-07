# entra-docker-sync

A script that provisions Docker containers on Linux hosts based on Entra ID (Azure AD) group memberships, automatically spinning up or tearing down containers when users join or leave Microsoft 365 security groups.

## Requirements

- Python 3.9+
- Docker Engine running on the host
- Microsoft Entra ID app registration with `GroupMember.Read.All` and `Directory.Read.All` permissions
- Terraform (optional, for state tracking)

## Installation

```bash
git clone https://github.com/your-org/entra-docker-sync.git
cd entra-docker-sync
pip install -r requirements.txt
```

Copy and configure the environment file:

```bash
cp .env.example .env
# Edit .env with your Azure credentials and container mappings
```

## Configuration

Set the following environment variables (or use a `.env` file):

| Variable | Description |
|---|---|
| `AZURE_TENANT_ID` | Your Entra ID tenant ID |
| `AZURE_CLIENT_ID` | App registration client ID |
| `AZURE_CLIENT_SECRET` | App registration client secret |
| `GROUP_CONTAINER_MAP` | JSON mapping of group IDs to Docker images |
| `POLL_INTERVAL` | Seconds between polls (default: 60) |
| `STATE_DIR` | Directory to store Terraform-style state files |

## Usage

```bash
# Run the sync daemon
python -m entra_docker_sync.main

# Dry run (shows what would change, no Docker actions taken)
python -m entra_docker_sync.main --dry-run

# Generate a lifecycle event report
python -m entra_docker_sync.main --report --output report.txt

# Run a single sync cycle and exit
python -m entra_docker_sync.main --once
```

## Logs

Lifecycle events are written to `sync.log` in the working directory and include the Entra user UPN, group name, container ID, and action taken.
