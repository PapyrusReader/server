"""Decode deployment YAML with the exact deployed PowerSync image, without network access."""

import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VALIDATE = """
import { readFileSync } from 'node:fs';
import { ConfigCollector } from '/app/packages/service-core/dist/util/config/collectors/config-collector.js';
const collector = new ConfigCollector();
const config = collector.decode(collector.parseYaml(readFileSync(0, 'utf8')));
collector.validate(config);
console.log('PowerSync deployment configuration decoded and validated.');
"""


def main() -> None:
    image = re.search(r"image: (journeyapps/powersync-service:\S+)", (ROOT / "deploy/compose.yml").read_text())

    if image is None:
        raise ValueError("PowerSync deployment image was not found")

    command = ["docker", "run", "--rm", "-i", "--network", "none", "--entrypoint", "node"]

    for name, value in {
        "PS_DATA_SOURCE_URI": "postgresql://test:test@database:5432/papyrus_test",
        "PS_STORAGE_URI": "postgresql://test:test@storage:5432/powersync_test",
        "PS_JWKS_URI": "http://api:8080/v1/auth/jwks",
        "PS_AUDIENCE": "papyrus-test",
    }.items():
        command.extend(["-e", f"{name}={value}"])

    command.extend([image.group(1), "--input-type=module", "-e", VALIDATE])
    subprocess.run(command, input=(ROOT / "powersync/service.yaml").read_text(), text=True, check=True)


if __name__ == "__main__":
    main()
