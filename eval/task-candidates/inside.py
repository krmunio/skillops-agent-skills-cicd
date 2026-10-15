"""Container-only entrypoint. Repository imports happen only after isolation checks."""

import importlib
import importlib.metadata
import json
import os
from pathlib import Path
import platform
import shutil
import socket
import subprocess
import sys
import xml.etree.ElementTree as ET

assert os.getuid() != 0
assert socket.if_nameindex() == [(1, "lo")], "external network interface present"
status = Path("/proc/self/status").read_text()
assert "NoNewPrivs:\t1" in status
assert "CapEff:\t0000000000000000" in status
assert not Path("/var/run/docker.sock").exists()
source_root = Path("/src")
if sys.argv[1] == "urllib3":
    source_root = Path("/tmp/urllib3")
    shutil.copytree("/src", source_root)
    subprocess.run(
        [sys.executable, "-m", "hatchling", "build", "-t", "wheel"],
        cwd=source_root, check=True,
        env={**os.environ, "SETUPTOOLS_SCM_PRETEND_VERSION": "0.0.0+reproduction"},
    )
    sys.path.insert(0, str(source_root / "src"))
module = importlib.import_module(sys.argv[1])
assert Path(module.__file__).resolve().is_relative_to(source_root)
environment = {
    "python": sys.version,
    "architecture": platform.machine(),
    "uid": os.getuid(),
    "interfaces": socket.if_nameindex(),
    "module": module.__file__,
    "version_build": "offline hatchling, SETUPTOOLS_SCM_PRETEND_VERSION=0.0.0+reproduction" if sys.argv[1] == "urllib3" else None,
    "dependencies": sorted(
        (dist.metadata["Name"], dist.version) for dist in importlib.metadata.distributions(path=["/deps"])
    ),
}
if sys.argv[1] == "rq":
    from redis import Redis

    environment["redis_server"] = Redis().info("server")["redis_version"]
print("ENVIRONMENT " + json.dumps(environment), flush=True)

import pytest

code = pytest.main([
    "-v", "--tb=short", "--noconftest", "-c", "/dev/null", "-p", "no:cacheprovider",
    "--junitxml=/tmp/results.xml", "/checks/check.py",
])
root = ET.parse("/tmp/results.xml").getroot()
cases = []
for test in root.iter("testcase"):
    cases.append({
        "name": test.attrib["name"],
        "class": test.attrib.get("classname"),
        "outcome": next((tag for tag in ("failure", "error", "skipped") if test.find(tag) is not None), "passed"),
        "detail": "".join(test.find(tag).text or "" for tag in ("failure", "error") if test.find(tag) is not None),
    })
print("RESULT_JSON " + json.dumps({"exit_code": int(code), "environment": environment, "tests": cases}))
sys.exit(code)
