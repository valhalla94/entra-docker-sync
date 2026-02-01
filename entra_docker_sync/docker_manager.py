"""Docker container lifecycle management module."""

import subprocess
import logging
from typing import Optional

logger = logging.getLogger(__name__)


class DockerManager:
    """Manages Docker container start/stop operations on the local host."""

    def __init__(self, docker_binary: str = "docker"):
        self.docker_binary = docker_binary
        self._verify_docker_available()

    def _verify_docker_available(self) -> None:
        """Ensure the Docker binary is accessible."""
        try:
            result = subprocess.run(
                [self.docker_binary, "version", "--format", "{{.Server.Version}}"],
                capture_output=True,
                text=True,
                timeout=10,
            )
            if result.returncode != 0:
                raise EnvironmentError(
                    f"Docker daemon not reachable: {result.stderr.strip()}"
                )
            logger.debug("Docker version: %s", result.stdout.strip())
        except FileNotFoundError:
            raise EnvironmentError(
                f"Docker binary not found at '{self.docker_binary}'. "
                "Ensure Docker is installed and on PATH."
            )

    def start_container(self, container_name: str, image: str, env_vars: Optional[dict] = None) -> bool:
        """Start a Docker container. Returns True on success."""
        cmd = [self.docker_binary, "run", "-d", "--name", container_name]
        if env_vars:
            for key, value in env_vars.items():
                cmd.extend(["-e", f"{key}={value}"])
        cmd.append(image)

        logger.info("Starting container '%s' from image '%s'", container_name, image)
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            logger.error(
                "Failed to start container '%s': %s", container_name, result.stderr.strip()
            )
            return False
        logger.info("Container '%s' started (id=%s)", container_name, result.stdout.strip()[:12])
        return True

    def stop_container(self, container_name: str, remove: bool = True) -> bool:
        """Stop (and optionally remove) a running Docker container."""
        logger.info("Stopping container '%s'", container_name)
        stop_result = subprocess.run(
            [self.docker_binary, "stop", container_name],
            capture_output=True,
            text=True,
        )
        if stop_result.returncode != 0:
            logger.warning(
                "Could not stop container '%s': %s",
                container_name,
                stop_result.stderr.strip(),
            )
            return False

        if remove:
            rm_result = subprocess.run(
                [self.docker_binary, "rm", container_name],
                capture_output=True,
                text=True,
            )
            if rm_result.returncode != 0:
                logger.warning(
                    "Could not remove container '%s': %s",
                    container_name,
                    rm_result.stderr.strip(),
                )
        return True

    def list_running_containers(self) -> list[str]:
        """Return names of all currently running containers."""
        result = subprocess.run(
            [self.docker_binary, "ps", "--format", "{{.Names}}"],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            logger.error("Failed to list containers: %s", result.stderr.strip())
            return []
        names = [line.strip() for line in result.stdout.splitlines() if line.strip()]
        logger.debug("Running containers: %s", names)
        return names

    def container_exists(self, container_name: str) -> bool:
        """Check whether a container (running or stopped) exists by name."""
        result = subprocess.run(
            [
                self.docker_binary,
                "ps",
                "-a",
                "--filter",
                f"name=^{container_name}$",
                "--format",
                "{{.Names}}",
            ],
            capture_output=True,
            text=True,
        )
        return container_name in result.stdout.splitlines()
