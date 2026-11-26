import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "deploy"))
from release_job import RELEASE_FILES, JobError, check_release, deploy  # noqa: E402


class ReleaseJobTests(unittest.TestCase):
    def setUp(self) -> None:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        root = Path(directory.name)
        self.checkout = root / "server"
        self.checkout.mkdir()
        self.git("init", "--quiet", "-b", "master")
        self.git("config", "user.name", "Release Test")
        self.git("config", "user.email", "test@example.test")
        self.git("config", "commit.gpgsign", "false")
        self.directory = self.checkout / "deploy"
        self.directory.mkdir()
        (self.directory / ".gitignore").write_text("production.env\njob.log\n.release-job.lock\n")
        self.settings = self.directory / "production.env"
        self.settings.write_text("PAPYRUS_VERSION=1.0.0\nOTHER=example\n")
        self.settings.chmod(0o600)

        for filename in RELEASE_FILES:
            path = self.checkout / filename
            path.parent.mkdir(exist_ok=True)
            path.write_text("previous configuration\n")

        (self.directory / "deploy.sh").write_text(
            'set -eu\nprintf "deploy %s %s\\n" "$PAPYRUS_VERSION" "$PAPYRUS_SERVER_IMAGE" >> job.log\n'
        )
        self.git("add", ".")
        self.git("commit", "--quiet", "-m", "Previous deployment")
        self.previous = self.git("rev-parse", "HEAD")
        self.production = root / "production"
        shutil.copytree(self.checkout, self.production, ignore=shutil.ignore_patterns(".git"))
        (self.directory / "release.txt").write_text("new configuration\n")
        (self.directory / "compose.yml").write_text("new configuration\n")
        self.git("add", "deploy/release.txt", "deploy/compose.yml")
        self.git("commit", "--quiet", "-m", "New release")
        revision = self.git("rev-parse", "HEAD")
        remote = root / "server.git"
        self.git("clone", "--quiet", "--bare", str(self.checkout), str(remote))
        self.git("remote", "add", "origin", str(remote))
        self.git("checkout", "--quiet", "--detach", self.previous)
        image = "ghcr.io/papyrusreader/server@sha256:" + "a" * 64
        self.request = {"version": "1.0.1", "revision": revision, "image": image}
        workspace = self.checkout.parent / "workspace"
        workspace.mkdir()

        self.workspace_git("init", "--quiet", "-b", "master")
        self.workspace_git("config", "user.name", "Release Test")
        self.workspace_git("config", "user.email", "test@example.test")
        self.workspace_git("config", "commit.gpgsign", "false")
        (workspace / "release.json").write_text(json.dumps({"version": "1.0.1", "orchestrator": "octopus"}))
        self.workspace_git("add", "release.json")
        self.workspace_git("update-index", "--add", "--cacheinfo", f"160000,{revision},server")
        self.workspace_git("commit", "-qm", "Authorize selected server")
        self.request["manifest"] = self.workspace_git("rev-parse", "HEAD")
        self.backup = root / "backup-check"
        self.backup.write_text(
            "#!/bin/sh\nset -eu\ngrep -qx PAPYRUS_VERSION=1.0.0 production.env\n"
            'test "$(git -C "$FIXTURE_CHECKOUT" rev-parse HEAD)" = "$FIXTURE_OLD_REVISION"\n'
            "printf 'backup\n' >> job.log\ncat >/dev/null\n"
        )
        self.backup.chmod(0o700)
        binary = root / "docker"
        binary.write_text(
            '#!/bin/sh\nset -eu\ncase "$1" in\n'
            'pull) test "$2" = "$FIXTURE_IMAGE" ;;\n'
            'inspect) printf "%s\\n" "$FIXTURE_OLD_REVISION" ;;\n'
            'image) printf \'{"org.opencontainers.image.revision":"%s",'
            '"org.opencontainers.image.version":"1.0.1"}\\n\' "$FIXTURE_NEW_REVISION" ;;\n'
            'compose) printf \'{"services":{"api":{"image":"%s"},"migrate":{"image":"%s"}}}\\n\' '
            '"$PAPYRUS_SERVER_IMAGE" "$PAPYRUS_SERVER_IMAGE" ;;\n'
            "*) exit 1 ;;\nesac\n"
        )
        binary.chmod(0o700)
        environment = patch.dict(
            os.environ,
            {
                "PATH": f"{root}{os.pathsep}{os.environ['PATH']}",
                "FIXTURE_OLD_REVISION": self.previous,
                "FIXTURE_NEW_REVISION": revision,
                "FIXTURE_IMAGE": image,
                "FIXTURE_CHECKOUT": str(self.checkout),
                "GIT_CONFIG_COUNT": "1",
                "GIT_CONFIG_KEY_0": f"url.{workspace.as_uri()}.insteadOf",
                "GIT_CONFIG_VALUE_0": "https://github.com/PapyrusReader/papyrus.git",
            },
        )
        environment.start()
        self.addCleanup(environment.stop)

    def workspace_git(self, *args: str) -> str:
        return subprocess.check_output(["git", *args], cwd=self.checkout.parent / "workspace", text=True).strip()

    def git(self, *args: str) -> str:
        return subprocess.check_output(["git", *args], cwd=self.checkout, text=True).strip()

    def test_pins_both_services_and_checks_backup_before_deploy(self) -> None:
        deploy(self.checkout, self.backup, self.request)
        self.assertEqual(self.git("rev-parse", "HEAD"), self.request["revision"])
        self.assertEqual(self.settings.read_text(), "PAPYRUS_VERSION=1.0.1\nOTHER=example\n")
        self.assertEqual(self.settings.stat().st_mode & 0o777, 0o600)
        actions = (self.directory / "job.log").read_text().splitlines()
        self.assertEqual(actions, ["backup", f"deploy 1.0.1 {self.request['image']}"])

    def test_backup_failure_leaves_source_and_settings_at_previous_deployment(self) -> None:
        self.backup.write_text("#!/bin/sh\nexit 1\n")

        with self.assertRaises(subprocess.CalledProcessError):
            deploy(self.checkout, self.backup, self.request)

        self.assertEqual(self.git("rev-parse", "HEAD"), self.previous)
        self.assertIn("PAPYRUS_VERSION=1.0.0", self.settings.read_text())
        self.assertFalse((self.directory / "job.log").exists())

    def test_wrong_revision_and_mutable_image_fail_before_backup(self) -> None:
        for overrides, message in (
            ({"revision": self.previous}, "approved workspace manifest"),
            ({"manifest": None}, "full workspace manifest"),
            ({"image": "ghcr.io/papyrusreader/server:1.0.1"}, "image digest"),
        ):
            with self.subTest(overrides=overrides), self.assertRaisesRegex(JobError, message):
                deploy(self.checkout, self.backup, self.request | overrides)

        self.assertFalse((self.directory / "job.log").exists())

    def test_workspace_master_authorizes_only_its_pinned_revision_and_version(self) -> None:
        revision = self.request["revision"]
        manifest = self.request["manifest"]
        workspace = self.checkout.parent / "workspace"
        check_release(self.checkout, revision, "1.0.1", manifest)

        for wrong_revision, wrong_version in ((self.previous, "1.0.1"), (revision, "1.0.2")):
            with self.assertRaisesRegex(JobError, "approved workspace manifest"):
                check_release(self.checkout, wrong_revision, wrong_version, manifest)

        self.workspace_git("commit", "--allow-empty", "-qm", "Unrelated workspace work")
        check_release(self.checkout, revision, "1.0.1", manifest)
        (workspace / "release.json").write_text(json.dumps({"version": "1.0.2", "orchestrator": "octopus"}))
        self.workspace_git("commit", "-qam", "Supersede release")

        with self.assertRaisesRegex(JobError, "no longer authorizes"):
            check_release(self.checkout, revision, "1.0.1", manifest)

    def test_registry_image_must_label_the_requested_revision(self) -> None:
        with (
            patch.dict(os.environ, {"FIXTURE_NEW_REVISION": self.previous}),
            self.assertRaisesRegex(JobError, "does not identify"),
        ):
            deploy(self.checkout, self.backup, self.request)

        self.assertFalse((self.directory / "job.log").exists())

    def test_copied_deployment_updates_release_files_and_preserves_local_settings(self) -> None:
        local = self.production / "deploy/local-config"
        local.write_text("keep\n")
        deploy(self.checkout, self.backup, self.request, deployment=self.production)

        self.assertEqual((self.production / "deploy/compose.yml").read_text(), "new configuration\n")
        self.assertEqual(
            (self.production / "deploy/production.env").read_text(), "PAPYRUS_VERSION=1.0.1\nOTHER=example\n"
        )
        self.assertEqual(local.read_text(), "keep\n")

    def test_copied_deployment_stops_when_live_files_have_drifted(self) -> None:
        changed = self.production / "deploy/Caddyfile"
        changed.write_text("host override\n")

        with self.assertRaisesRegex(JobError, "Production file differs from the running release: deploy/Caddyfile"):
            deploy(self.checkout, self.backup, self.request, deployment=self.production)

        self.assertEqual(changed.read_text(), "host override\n")
        self.assertFalse((self.production / "deploy/job.log").exists())
