#!/usr/bin/env python3
"""Deploy a published server revision on a configured production host."""

import argparse
import fcntl
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

RELEASE_FILES = (
    "deploy/Caddyfile",
    "deploy/bootstrap-powersync.sql",
    "deploy/compose.yml",
    "deploy/deploy.sh",
    "deploy/validate_config.py",
    "powersync/service.yaml",
    "powersync/sync-config.yaml",
)


class JobError(RuntimeError):
    pass


def request_fields(request: object) -> tuple[str, str, str]:
    if not isinstance(request, dict):
        raise JobError("Expected a release request object")

    version = request.get("version", "")
    revision = request.get("revision", "")
    image = request.get("image", "")

    if not isinstance(version, str) or not re.fullmatch(r"\d+\.\d+\.\d+", version):
        raise JobError("Expected a server release version")

    if not isinstance(revision, str) or not re.fullmatch(r"[0-9a-f]{40}", revision):
        raise JobError("Expected a full server revision")

    if not isinstance(image, str) or not re.fullmatch(r"ghcr\.io/papyrusreader/server@sha256:[0-9a-f]{64}", image):
        raise JobError("Expected a published server image digest")

    return version, revision, image


def set_version(path: Path, version: str) -> None:
    content = path.read_text()
    updated, count = re.subn(r"(?m)^PAPYRUS_VERSION=.*$", f"PAPYRUS_VERSION={version}", content)

    if count != 1:
        raise JobError("production.env must contain one PAPYRUS_VERSION entry")

    replace_file(path, updated.encode())


def replace_file(path: Path, content: bytes) -> None:
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as temporary:
        written = Path(temporary.name)

        try:
            temporary.write(content)
            temporary.flush()
            os.fsync(temporary.fileno())
            written.chmod(path.stat().st_mode & 0o777)
            written.replace(path)
        finally:
            written.unlink(missing_ok=True)


def check_release(checkout: Path, revision: str, version: str, manifest: str | None) -> None:
    if not isinstance(manifest, str) or not re.fullmatch(r"[0-9a-f]{40}", manifest):
        raise JobError("Expected a full workspace manifest revision")

    workspace_ref = "refs/papyrus/workspace-master"

    subprocess.run(
        [
            "git",
            "fetch",
            "--quiet",
            "--no-tags",
            "--no-recurse-submodules",
            "https://github.com/PapyrusReader/papyrus.git",
            f"+refs/heads/master:{workspace_ref}",
        ],
        cwd=checkout,
        check=True,
    )

    def read(*args: str) -> str:
        return subprocess.check_output(["git", *args], cwd=checkout, text=True).strip()

    ancestry = subprocess.run(["git", "merge-base", "--is-ancestor", manifest, workspace_ref], cwd=checkout)
    changed = subprocess.run(
        ["git", "diff", "--quiet", manifest, workspace_ref, "--", "release.json", "client", "server", "reader"],
        cwd=checkout,
    )

    if ancestry.returncode or changed.returncode:
        raise JobError("Workspace master no longer authorizes this release")

    settings = json.loads(read("show", f"{manifest}:release.json"))
    entry = read("ls-tree", manifest, "server")

    if (
        settings.get("orchestrator") != "octopus"
        or settings.get("version") != version
        or entry != f"160000 commit {revision}\tserver"
    ):
        raise JobError("Server revision or version differs from the approved workspace manifest")

    subprocess.run(
        ["git", "fetch", "--quiet", "--no-tags", "--no-recurse-submodules", "origin", revision],
        cwd=checkout,
        check=True,
    )


def require_clean_checkout(checkout: Path) -> None:
    dirty = subprocess.check_output(
        ["git", "status", "--porcelain", "--untracked-files=no"],
        cwd=checkout,
        text=True,
    )

    if dirty:
        raise JobError("Server checkout has tracked changes")


def verify_copied_files(checkout: Path, deployment: Path, container: str) -> None:
    revision = subprocess.check_output(
        ["docker", "inspect", "--format", '{{index .Config.Labels "org.opencontainers.image.revision"}}', container],
        text=True,
    ).strip()

    if not re.fullmatch(r"[0-9a-f]{40}", revision):
        raise JobError("Could not identify the running server revision")

    for filename in RELEASE_FILES:
        deployed = deployment / filename
        published = subprocess.check_output(["git", "show", f"{revision}:{filename}"], cwd=checkout)

        if not deployed.is_file() or deployed.is_symlink() or deployed.read_bytes() != published:
            raise JobError(f"Production file differs from the running release: {filename}")


def deploy(
    checkout: Path,
    backup_check: Path,
    request: object,
    *,
    deployment: Path | None = None,
    container: str = "papyrus-production-api-1",
) -> None:
    version, revision, image = request_fields(request)
    manifest = request.get("manifest") if isinstance(request, dict) else None
    directory = (deployment or checkout) / "deploy"
    settings = directory / "production.env"

    if not settings.is_file() or settings.is_symlink():
        raise JobError("Expected a regular production.env in the server checkout")

    if not backup_check.is_file() or not os.access(backup_check, os.X_OK):
        raise JobError("The backup and restore check executable is unavailable")

    with (directory / ".release-job.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise JobError("Another server deployment job is running") from error

        require_clean_checkout(checkout)
        check_release(checkout, revision, version, manifest)

        if deployment is not None:
            verify_copied_files(checkout, deployment, container)

        subprocess.run(["docker", "pull", image], check=True, cwd=directory)
        labels = subprocess.check_output(
            ["docker", "image", "inspect", "--format", "{{json .Config.Labels}}", image],
            cwd=directory,
            text=True,
        )
        metadata = json.loads(labels) or {}

        if (
            metadata.get("org.opencontainers.image.revision") != revision
            or metadata.get("org.opencontainers.image.version") != version
        ):
            raise JobError("Pulled server image does not identify the requested release")

        subprocess.run(
            [str(backup_check)],
            cwd=directory,
            input=json.dumps(request),
            text=True,
            check=True,
            pass_fds=(lock.fileno(),),
        )
        require_clean_checkout(checkout)
        check_release(checkout, revision, version, manifest)

        if deployment is not None:
            verify_copied_files(checkout, deployment, container)

        subprocess.run(
            ["git", "checkout", "--quiet", "--no-overwrite-ignore", "--detach", revision], cwd=checkout, check=True
        )
        environment = {**os.environ, "PAPYRUS_VERSION": version, "PAPYRUS_SERVER_IMAGE": image}
        configuration = subprocess.check_output(
            [
                "docker",
                "compose",
                "--env-file",
                "production.env",
                "-f",
                str(checkout / "deploy/compose.yml"),
                "--project-directory",
                str(directory),
                "--profile",
                "maintenance",
                "config",
                "--format",
                "json",
            ],
            cwd=directory,
            env=environment,
            text=True,
        )
        services = json.loads(configuration)["services"]

        if any(services[name]["image"] != image for name in ("api", "migrate")):
            raise JobError("Released Compose configuration must pin API and migrations to the published digest")

        if deployment is not None:
            sources = {filename: (checkout / filename).read_bytes() for filename in RELEASE_FILES}

            for filename, content in sources.items():
                replace_file(deployment / filename, content)

        set_version(settings, version)
        subprocess.run(["sh", "deploy.sh"], cwd=directory, env=environment, check=True, pass_fds=(lock.fileno(),))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--checkout", type=Path, required=True)
    parser.add_argument("--backup-check", type=Path, required=True)
    parser.add_argument("--deployment-root", type=Path, help="Production directory populated from release files")
    parser.add_argument("--container", default="papyrus-production-api-1")
    options = parser.parse_args()

    try:
        deployment = options.deployment_root.resolve() if options.deployment_root else None
        deploy(
            options.checkout.resolve(),
            options.backup_check.resolve(),
            json.load(sys.stdin),
            deployment=deployment,
            container=options.container,
        )
    except (JobError, OSError, subprocess.CalledProcessError, ValueError, KeyError) as error:
        print(f"Server release job: {error}", file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
