#!/usr/bin/env python3
"""entra-docker-sync: Provision Docker containers based on Entra ID group memberships.

Polls Microsoft Graph API for group membership changes, starts/stops Docker
containers accordingly, persists state as JSON (Terraform-style), and logs
all lifecycle events tied to Entra user identities.
"""

import argparse
import json
import logging
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import docker
import msal
import requests
from dotenv import load_dotenv

load_dotenv()

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
LOG_FILE = os.getenv("LOG_FILE", "sync.log")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE),
        logging.StreamHandler(sys.stdout),
    ],
)
log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
TENANT_ID = os.environ["AZURE_TENANT_ID"]
CLIENT_ID = os.environ["AZURE_CLIENT_ID"]
CLIENT_SECRET = os.environ["AZURE_CLIENT_SECRET"]
POLL_INTERVAL = int(os.getenv("POLL_INTERVAL", "60"))
STATE_DIR = Path(os.getenv("STATE_DIR", "./state"))
GRAPH_SCOPES = ["https://graph.microsoft.com/.default"]
GRAPH_BASE = "https://graph.microsoft.com/v1.0"

# GROUP_CONTAINER_MAP: {"<group-object-id>": {"image": "nginx:alpine", "name_prefix": "web"}}
GROUP_CONTAINER_MAP: dict[str, dict] = json.loads(
    os.getenv("GROUP_CONTAINER_MAP", "{}")
)

# ---------------------------------------------------------------------------
# Microsoft Graph helpers
# ---------------------------------------------------------------------------

def _get_access_token() -> str:
    """Acquire an OAuth2 client-credentials token from Entra ID."""
    authority = f"https://login.microsoftonline.com/{TENANT_ID}"
    app = msal.ConfidentialClientApplication(
        CLIENT_ID, authority=authority, client_credential=CLIENT_SECRET
    )
    result = app.acquire_token_for_client(scopes=GRAPH_SCOPES)
    if "access_token" not in result:
        raise RuntimeError(
            f"Failed to acquire token: {result.get('error_description', result)}"
        )
    return result["access_token"]


def get_group_members(token: str, group_id: str) -> list[dict]:
    """Return all transitive members of an Entra ID group.

    Each member dict contains at least: id, userPrincipalName, displayName.
    """
    url = f"{GRAPH_BASE}/groups/{group_id}/transitiveMembers"
    headers = {"Authorization": f"Bearer {token}"}
    members: list[dict] = []

    while url:
        resp = requests.get(url, headers=headers, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        members.extend(
            m for m in data.get("value", []) if m.get("@odata.type") == "#microsoft.graph.user"
        )
        url = data.get("@odata.nextLink")  # pagination

    return members


# ---------------------------------------------------------------------------
# State management (Terraform-style JSON)
# ---------------------------------------------------------------------------

StateFile = dict[str, Any]  # {user_id: {container_id, image, started_at, upn}}


def _state_path(group_id: str) -> Path:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    return STATE_DIR / f"{group_id}.json"


def load_state(group_id: str) -> StateFile:
    path = _state_path(group_id)
    if path.exists():
        return json.loads(path.read_text())
    return {}


def save_state(group_id: str, state: StateFile) -> None:
    _state_path(group_id).write_text(json.dumps(state, indent=2))


# ---------------------------------------------------------------------------
# Docker helpers
# ---------------------------------------------------------------------------

def get_docker_client() -> docker.DockerClient:
    return docker.from_env()


def start_container(
    client: docker.DockerClient,
    image: str,
    name: str,
    dry_run: bool = False,
) -> str:
    """Pull image if needed and start a detached container; return container ID."""
    if dry_run:
        log.info("[DRY-RUN] Would start container name=%s image=%s", name, image)
        return "dry-run-id"
    log.info("Starting container name=%s image=%s", name, image)
    container = client.containers.run(
        image,
        name=name,
        detach=True,
        restart_policy={"Name": "unless-stopped"},
    )
    return container.id[:12]


def stop_container(
    client: docker.DockerClient,
    container_id: str,
    dry_run: bool = False,
) -> None:
    """Stop and remove a container by ID."""
    if dry_run:
        log.info("[DRY-RUN] Would stop/remove container id=%s", container_id)
        return
    try:
        container = client.containers.get(container_id)
        log.info("Stopping container id=%s name=%s", container.id[:12], container.name)
        container.stop(timeout=10)
        container.remove()
    except docker.errors.NotFound:
        log.warning("Container %s not found; skipping removal", container_id)


# ---------------------------------------------------------------------------
# Core sync logic
# ---------------------------------------------------------------------------

def sync_group(
    client: docker.DockerClient,
    token: str,
    group_id: str,
    group_cfg: dict,
    dry_run: bool = False,
) -> list[dict]:
    """Sync one group: start containers for new members, stop for removed.

    Returns a list of lifecycle event dicts for the report.
    """
    events: list[dict] = []
    image = group_cfg["image"]
    name_prefix = group_cfg.get("name_prefix", "sync")

    current_members = get_group_members(token, group_id)
    current_ids = {m["id"]: m for m in current_members}
    state = load_state(group_id)

    # Provision containers for new members
    for user_id, member in current_ids.items():
        if user_id not in state:
            upn = member.get("userPrincipalName", user_id)
            container_name = f"{name_prefix}-{user_id[:8]}"
            cid = start_container(client, image, container_name, dry_run=dry_run)
            ts = datetime.now(timezone.utc).isoformat()
            state[user_id] = {
                "container_id": cid,
                "container_name": container_name,
                "image": image,
                "started_at": ts,
                "upn": upn,
                "display_name": member.get("displayName", ""),
            }
            events.append({
                "action": "started",
                "group_id": group_id,
                "user_upn": upn,
                "container_name": container_name,
                "container_id": cid,
                "timestamp": ts,
            })
            log.info("STARTED  user=%s container=%s", upn, container_name)

    # Tear down containers for removed members
    removed_ids = [uid for uid in state if uid not in current_ids]
    for user_id in removed_ids:
        entry = state.pop(user_id)
        cid = entry["container_id"]
        stop_container(client, cid, dry_run=dry_run)
        ts = datetime.now(timezone.utc).isoformat()
        events.append({
            "action": "stopped",
            "group_id": group_id,
            "user_upn": entry["upn"],
            "container_name": entry["container_name"],
            "container_id": cid,
            "timestamp": ts,
        })
        log.info("STOPPED  user=%s container=%s", entry["upn"], entry["container_name"])

    save_state(group_id, state)
    return events


# ---------------------------------------------------------------------------
# Report generation
# ---------------------------------------------------------------------------

def write_report(events: list[dict], output_path: str) -> None:
    """Write a human-readable lifecycle event report."""
    lines = [
        "entra-docker-sync Lifecycle Report",
        f"Generated: {datetime.now(timezone.utc).isoformat()}",
        "-" * 72,
    ]
    if not events:
        lines.append("No lifecycle events recorded in this session.")
    for ev in events:
        lines.append(
            f"[{ev['timestamp']}] {ev['action'].upper():8s} "
            f"user={ev['user_upn']}  "
            f"container={ev['container_name']} ({ev['container_id']})"
        )
    report = "\n".join(lines) + "\n"
    Path(output_path).write_text(report)
    log.info("Report written to %s (%d events)", output_path, len(events))


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Sync Docker containers with Entra ID group memberships."
    )
    p.add_argument("--dry-run", action="store_true", help="Show changes without acting")
    p.add_argument("--once", action="store_true", help="Run a single sync cycle and exit")
    p.add_argument("--report", action="store_true", help="Generate lifecycle report after sync")
    p.add_argument("--output", default="report.txt", help="Report output file (default: report.txt)")
    return p.parse_args()


def run_cycle(
    docker_client: docker.DockerClient,
    dry_run: bool = False,
) -> list[dict]:
    """Execute one full poll-and-sync cycle across all configured groups."""
    if not GROUP_CONTAINER_MAP:
        log.warning("GROUP_CONTAINER_MAP is empty; nothing to sync. Set the env var.")
        return []
    token = _get_access_token()
    all_events: list[dict] = []
    for group_id, cfg in GROUP_CONTAINER_MAP.items():
        log.info("Syncing group %s (image=%s)", group_id, cfg.get("image", "?"))
        try:
            events = sync_group(docker_client, token, group_id, cfg, dry_run=dry_run)
            all_events.extend(events)
        except requests.HTTPError as exc:
            log.error("Graph API error for group %s: %s", group_id, exc)
        except docker.errors.DockerException as exc:
            log.error("Docker error for group %s: %s", group_id, exc)
    return all_events


def main() -> None:
    args = parse_args()
    docker_client = get_docker_client()
    all_events: list[dict] = []

    log.info(
        "entra-docker-sync starting (dry_run=%s, once=%s, poll_interval=%ss)",
        args.dry_run, args.once, POLL_INTERVAL,
    )

    try:
        while True:
            events = run_cycle(docker_client, dry_run=args.dry_run)
            all_events.extend(events)
            if args.once:
                break
            log.info("Sleeping %s seconds until next poll...", POLL_INTERVAL)
            time.sleep(POLL_INTERVAL)
    except KeyboardInterrupt:
        log.info("Interrupted by user.")

    if args.report:
        write_report(all_events, args.output)


if __name__ == "__main__":
    main()
