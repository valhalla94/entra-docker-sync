"""Report generator for container lifecycle events tied to Entra ID identities."""

import csv
import json
import logging
import os
from datetime import datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


class ReportGenerator:
    """Generates lifecycle event reports linking Docker containers to Entra ID users."""

    def __init__(self, log_dir: str = "logs"):
        self.log_dir = Path(log_dir)
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self.events: list[dict[str, Any]] = []

    def record_event(
        self,
        action: str,
        container_name: str,
        container_id: str | None,
        user_upn: str,
        user_display_name: str,
        group_id: str,
        group_name: str,
        status: str,
        error: str | None = None,
    ) -> None:
        """Record a container lifecycle event.

        Args:
            action: The action performed (start, stop, create, remove).
            container_name: Name of the Docker container.
            container_id: Docker container ID if available.
            user_upn: User Principal Name from Entra ID.
            user_display_name: Display name of the user from Entra ID.
            group_id: Entra ID group object ID.
            group_name: Display name of the Entra ID group.
            status: Outcome of the action (success, failed, skipped).
            error: Optional error message if action failed.
        """
        event = {
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "action": action,
            "container_name": container_name,
            "container_id": container_id or "",
            "user_upn": user_upn,
            "user_display_name": user_display_name,
            "group_id": group_id,
            "group_name": group_name,
            "status": status,
            "error": error or "",
        }
        self.events.append(event)
        logger.info(
            "Event recorded: action=%s container=%s user=%s status=%s",
            action,
            container_name,
            user_upn,
            status,
        )

    def write_json_report(self, filename: str | None = None) -> Path:
        """Write all recorded events to a JSON report file.

        Args:
            filename: Optional filename override. Defaults to timestamped name.

        Returns:
            Path to the written report file.
        """
        if filename is None:
            ts = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
            filename = f"lifecycle_report_{ts}.json"

        report_path = self.log_dir / filename
        report = {
            "generated_at": datetime.utcnow().isoformat() + "Z",
            "total_events": len(self.events),
            "summary": self._build_summary(),
            "events": self.events,
        }

        with open(report_path, "w", encoding="utf-8") as fh:
            json.dump(report, fh, indent=2)

        logger.info("JSON report written to %s (%d events)", report_path, len(self.events))
        return report_path

    def write_csv_report(self, filename: str | None = None) -> Path:
        """Write all recorded events to a CSV report file.

        Args:
            filename: Optional filename override. Defaults to timestamped name.

        Returns:
            Path to the written report file.
        """
        if filename is None:
            ts = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
            filename = f"lifecycle_report_{ts}.csv"

        report_path = self.log_dir / filename
        fieldnames = [
            "timestamp",
            "action",
            "container_name",
            "container_id",
            "user_upn",
            "user_display_name",
            "group_id",
            "group_name",
            "status",
            "error",
        ]

        with open(report_path, "w", newline="", encoding="utf-8") as fh:
            writer = csv.DictWriter(fh, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(self.events)

        logger.info("CSV report written to %s (%d events)", report_path, len(self.events))
        return report_path

    def _build_summary(self) -> dict[str, Any]:
        """Build a summary of recorded events grouped by action and status.

        Returns:
            Dictionary containing counts grouped by action and status.
        """
        summary: dict[str, Any] = {
            "by_action": {},
            "by_status": {},
            "by_user": {},
            "failed_events": [],
        }

        for event in self.events:
            action = event["action"]
            status = event["status"]
            upn = event["user_upn"]

            summary["by_action"][action] = summary["by_action"].get(action, 0) + 1
            summary["by_status"][status] = summary["by_status"].get(status, 0) + 1
            summary["by_user"][upn] = summary["by_user"].get(upn, 0) + 1

            if status == "failed":
                summary["failed_events"].append({
                    "timestamp": event["timestamp"],
                    "container_name": event["container_name"],
                    "user_upn": upn,
                    "error": event["error"],
                })

        return summary

    def print_summary(self) -> None:
        """Print a human-readable summary of events to stdout."""
        summary = self._build_summary()
        print("\n=== Container Lifecycle Event Summary ===")
        print(f"Total events: {len(self.events)}")
        print("\nBy action:")
        for action, count in sorted(summary["by_action"].items()):
            print(f"  {action}: {count}")
        print("\nBy status:")
        for status, count in sorted(summary["by_status"].items()):
            print(f"  {status}: {count}")
        print("\nBy user (UPN):")
        for upn, count in sorted(summary["by_user"].items()):
            print(f"  {upn}: {count}")
        if summary["failed_events"]:
            print("\nFailed events:")
            for ev in summary["failed_events"]:
                print(f"  [{ev['timestamp']}] {ev['container_name']} ({ev['user_upn']}): {ev['error']}")
        print("========================================\n")

    def clear_events(self) -> None:
        """Clear all recorded events from memory."""
        self.events.clear()
        logger.debug("Event log cleared.")
