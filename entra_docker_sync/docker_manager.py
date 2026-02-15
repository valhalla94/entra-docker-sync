import subprocess
import logging
import json
import time
from typing import Optional

logger = logging.getLogger(__name__)


def run_docker_command(args: list, capture_output: bool = True) -> subprocess.CompletedProcess:
    """Run a docker CLI command and return the result."""
    cmd = ["docker"] + args
    logger.debug("Running docker command: %s", " ".join(cmd))
    result = subprocess.run(
        cmd,
        capture_output=capture_output,
        text=True
    )
    if result.returncode != 0:
        logger.error("Docker command failed: %s\nStderr: %s", " ".join(cmd), result.stderr)
    return result


def start_container(container_name: str, image: str, env_vars: dict = None, ports: dict = None) -> bool:
    """Start a Docker container with the given configuration."""
    args = ["run", "-d", "--name", container_name]

    if env_vars:
        for key, value in env_vars.items():
            args += ["-e", f"{key}={value}"]

    if ports:
        for host_port, container_port in ports.items():
            args += ["-p", f"{host_port}:{container_port}"]

    args.append(image)

    result = run_docker_command(args)
    if result.returncode == 0:
        logger.info("Container '%s' started successfully.", container_name)
        return True
    else:
        logger.error("Failed to start container '%s'.", container_name)
        return False


def stop_container(container_name: str, timeout: int = 10) -> bool:
    """Stop a running Docker container gracefully."""
    result = run_docker_command(["stop", "--time", str(timeout), container_name])
    if result.returncode == 0:
        logger.info("Container '%s' stopped successfully.", container_name)
        return True
    else:
        logger.error("Failed to stop container '%s'.", container_name)
        return False


def remove_container(container_name: str, force: bool = False) -> bool:
    """Remove a Docker container."""
    args = ["rm"]
    if force:
        args.append("-f")
    args.append(container_name)

    result = run_docker_command(args)
    if result.returncode == 0:
        logger.info("Container '%s' removed successfully.", container_name)
        return True
    else:
        logger.error("Failed to remove container '%s'.", container_name)
        return False


def get_container_status(container_name: str) -> Optional[str]:
    """Get the current status of a Docker container."""
    result = run_docker_command([
        "inspect",
        "--format", "{{.State.Status}}",
        container_name
    ])
    if result.returncode == 0:
        status = result.stdout.strip()
        logger.debug("Container '%s' status: %s", container_name, status)
        return status
    return None


def get_container_health(container_name: str) -> dict:
    """Retrieve health check status and details for a Docker container."""
    result = run_docker_command([
        "inspect",
        "--format",
        "{{json .State.Health}}",
        container_name
    ])

    if result.returncode != 0:
        logger.warning("Could not retrieve health info for container '%s'.", container_name)
        return {"status": "unknown", "failing_streak": 0, "log": []}

    raw = result.stdout.strip()
    if not raw or raw == "null":
        logger.debug("Container '%s' has no health check configured.", container_name)
        return {"status": "none", "failing_streak": 0, "log": []}

    try:
        health_data = json.loads(raw)
        return {
            "status": health_data.get("Status", "unknown"),
            "failing_streak": health_data.get("FailingStreak", 0),
            "log": [
                {
                    "start": entry.get("Start"),
                    "end": entry.get("End"),
                    "exit_code": entry.get("ExitCode"),
                    "output": entry.get("Output", "").strip()
                }
                for entry in health_data.get("Log", [])
            ]
        }
    except json.JSONDecodeError as exc:
        logger.error("Failed to parse health JSON for container '%s': %s", container_name, exc)
        return {"status": "parse_error", "failing_streak": 0, "log": []}


def wait_for_healthy(container_name: str, timeout: int = 60, interval: int = 5) -> bool:
    """Poll container health until healthy or timeout is reached."""
    elapsed = 0
    logger.info("Waiting for container '%s' to become healthy (timeout=%ds).", container_name, timeout)

    while elapsed < timeout:
        health = get_container_health(container_name)
        status = health.get("status")

        if status == "healthy":
            logger.info("Container '%s' is healthy after %ds.", container_name, elapsed)
            return True
        elif status == "unhealthy":
            failing_streak = health.get("failing_streak", 0)
            last_log = health["log"][-1] if health["log"] else {}
            logger.error(
                "Container '%s' is unhealthy (streak=%d). Last output: %s",
                container_name,
                failing_streak,
                last_log.get("output", "N/A")
            )
            return False
        elif status == "none":
            logger.debug("Container '%s' has no health check; skipping wait.", container_name)
            return True

        logger.debug(
            "Container '%s' health status is '%s'. Retrying in %ds...",
            container_name, status, interval
        )
        time.sleep(interval)
        elapsed += interval

    logger.warning("Timed out waiting for container '%s' to become healthy.", container_name)
    return False


def list_managed_containers(label: str = "entra-docker-sync=true") -> list:
    """List all containers managed by this tool using a Docker label filter."""
    result = run_docker_command([
        "ps", "-a",
        "--filter", f"label={label}",
        "--format", "{{json .}}"
    ])

    if result.returncode != 0:
        logger.error("Failed to list managed containers.")
        return []

    containers = []
    for line in result.stdout.strip().splitlines():
        try:
            containers.append(json.loads(line))
        except json.JSONDecodeError:
            logger.warning("Could not parse container entry: %s", line)

    logger.info("Found %d managed container(s).", len(containers))
    return containers
