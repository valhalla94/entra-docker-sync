import json
import os
import logging
from datetime import datetime
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)


class StateManager:
    """
    Manages Terraform-style state files to track which Docker containers
    were provisioned per Entra ID group assignment.
    """

    def __init__(self, state_dir: str = "./state"):
        self.state_dir = state_dir
        os.makedirs(self.state_dir, exist_ok=True)
        logger.info(f"StateManager initialized with state directory: {self.state_dir}")

    def _get_state_file_path(self, group_id: str) -> str:
        """Returns the path to the state file for a given group ID."""
        return os.path.join(self.state_dir, f"{group_id}.tfstate.json")

    def load_state(self, group_id: str) -> Dict:
        """
        Loads the state for a given Entra ID group.
        Returns an empty state dict if no state file exists.
        """
        state_file = self._get_state_file_path(group_id)
        if not os.path.exists(state_file):
            logger.debug(f"No state file found for group {group_id}, returning empty state.")
            return self._empty_state(group_id)

        try:
            with open(state_file, "r") as f:
                state = json.load(f)
            logger.debug(f"Loaded state for group {group_id}: {len(state.get('resources', []))} resources")
            return state
        except (json.JSONDecodeError, IOError) as e:
            logger.error(f"Failed to load state file for group {group_id}: {e}")
            return self._empty_state(group_id)

    def save_state(self, group_id: str, state: Dict) -> bool:
        """
        Persists the state for a given Entra ID group to a JSON state file.
        Returns True on success, False on failure.
        """
        state_file = self._get_state_file_path(group_id)
        state["last_updated"] = datetime.utcnow().isoformat() + "Z"

        try:
            with open(state_file, "w") as f:
                json.dump(state, f, indent=2)
            logger.info(f"State saved for group {group_id} -> {state_file}")
            return True
        except IOError as e:
            logger.error(f"Failed to save state file for group {group_id}: {e}")
            return False

    def add_container(self, group_id: str, container_id: str, container_name: str, user_id: str, user_upn: str) -> bool:
        """
        Adds a container resource entry to the group's state file.
        Tracks the Entra user identity that triggered provisioning.
        """
        state = self.load_state(group_id)
        resources = state.get("resources", [])

        # Avoid duplicate entries
        existing_ids = [r["container_id"] for r in resources]
        if container_id in existing_ids:
            logger.warning(f"Container {container_id} already tracked in state for group {group_id}")
            return False

        resource_entry = {
            "container_id": container_id,
            "container_name": container_name,
            "provisioned_by_user_id": user_id,
            "provisioned_by_upn": user_upn,
            "provisioned_at": datetime.utcnow().isoformat() + "Z",
            "status": "running"
        }

        resources.append(resource_entry)
        state["resources"] = resources
        logger.info(f"Tracking new container {container_name} ({container_id}) for group {group_id}, user {user_upn}")
        return self.save_state(group_id, state)

    def remove_container(self, group_id: str, container_id: str) -> bool:
        """
        Removes a container resource entry from the group's state file.
        Called when a container is stopped/removed due to group membership removal.
        """
        state = self.load_state(group_id)
        resources = state.get("resources", [])
        original_count = len(resources)

        state["resources"] = [r for r in resources if r["container_id"] != container_id]

        if len(state["resources"]) == original_count:
            logger.warning(f"Container {container_id} not found in state for group {group_id}")
            return False

        logger.info(f"Removed container {container_id} from state for group {group_id}")
        return self.save_state(group_id, state)

    def get_provisioned_containers(self, group_id: str) -> List[Dict]:
        """
        Returns the list of currently tracked container resources for a group.
        """
        state = self.load_state(group_id)
        return state.get("resources", [])

    def get_all_group_states(self) -> Dict[str, List[Dict]]:
        """
        Iterates over all .tfstate.json files in the state directory and
        returns a mapping of group_id -> list of container resources.
        """
        all_states = {}
        try:
            for filename in os.listdir(self.state_dir):
                if filename.endswith(".tfstate.json"):
                    group_id = filename.replace(".tfstate.json", "")
                    all_states[group_id] = self.get_provisioned_containers(group_id)
        except OSError as e:
            logger.error(f"Failed to list state directory {self.state_dir}: {e}")
        return all_states

    def mark_container_stopped(self, group_id: str, container_id: str) -> bool:
        """
        Updates a container's status to 'stopped' in the state file
        without fully removing the record, preserving audit history.
        """
        state = self.load_state(group_id)
        resources = state.get("resources", [])
        updated = False

        for resource in resources:
            if resource["container_id"] == container_id:
                resource["status"] = "stopped"
                resource["stopped_at"] = datetime.utcnow().isoformat() + "Z"
                updated = True
                break

        if not updated:
            logger.warning(f"Container {container_id} not found in state for group {group_id}")
            return False

        state["resources"] = resources
        logger.info(f"Marked container {container_id} as stopped in state for group {group_id}")
        return self.save_state(group_id, state)

    @staticmethod
    def _empty_state(group_id: str) -> Dict:
        """Returns a default empty state structure."""
        return {
            "format_version": "1.0",
            "group_id": group_id,
            "created_at": datetime.utcnow().isoformat() + "Z",
            "last_updated": None,
            "resources": []
        }
