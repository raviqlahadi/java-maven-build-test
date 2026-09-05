#!/usr/bin/env python3
"""Verify the Docker build engine end-to-end (v2).

Runs two synthetic builds through the REAL orchestrator code path:
  A) healthy Java 17 project  -> expect success
  B) project with ghost dep   -> expect failure + 'dependency' category

Usage:  ./venv/bin/python verify-docker.py
Requires: Docker daemon reachable, maven:3.9.6-eclipse-temurin-17 image.
"""
import os
import shutil
import sys
import tempfile

from importlib import import_module

import docker

OK_POM = """<?xml version="1.0"?>
<project xmlns="http://maven.apache.org/POM/4.0.0">
  <modelVersion>4.0.0</modelVersion>
  <groupId>research</groupId><artifactId>proj-ok</artifactId><version>1.0</version>
  <properties>
    <maven.compiler.source>17</maven.compiler.source>
    <maven.compiler.target>17</maven.compiler.target>
  </properties>
</project>"""

OK_SRC = """public class Hello {
    public static void main(String[] args) { System.out.println("build pipeline alive"); }
}"""

FAIL_POM = """<?xml version="1.0"?>
<project xmlns="http://maven.apache.org/POM/4.0.0">
  <modelVersion>4.0.0</modelVersion>
  <groupId>research</groupId><artifactId>proj-fail</artifactId><version>1.0</version>
  <properties>
    <maven.compiler.source>17</maven.compiler.source>
    <maven.compiler.target>17</maven.compiler.target>
  </properties>
  <dependencies>
    <dependency>
      <groupId>does.not.exist</groupId><artifactId>ghost-artifact</artifactId><version>9.9.9</version>
    </dependency>
  </dependencies>
</project>"""


def stage(root, rel, files):
    d = os.path.join(root, rel)
    os.makedirs(d, exist_ok=True)
    for name, content in files.items():
        path = os.path.join(d, name)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as f:
            f.write(content)
    return d


def main():
    bo = import_module("build-orchestrator")
    try:
        client = docker.from_env()
        client.ping()
    except Exception as e:
        print(f"❌ Docker not reachable: {e}")
        print("   Start Docker Desktop and wait for 'Engine running'.")
        return 1

    tmp = tempfile.mkdtemp(prefix="mvn-verify-")
    print(f"🔬 staging synthetic projects in {tmp}")
    ok_dir = stage(tmp, "proj-ok", {"pom.xml": OK_POM, "src/main/java/Hello.java": OK_SRC})
    fail_dir = stage(tmp, "proj-fail", {"pom.xml": FAIL_POM})

    print("\n=== A) healthy build (image auto-selected from pom properties) ===")
    pom = os.path.join(ok_dir, "pom.xml")
    print("detected image:", bo.get_jdk_image(pom))
    ok, reason, image = bo.run_maven_build(client, ok_dir)
    print(f"success={ok} | {reason} | image={image}")

    print("\n=== B) failing build (ghost dependency) ===")
    ok2, reason2, _ = bo.run_maven_build(client, fail_dir)
    print(f"success={ok2} | reason: {reason2[:120]}")
    print("category:", bo.classify_failure(reason2))

    shutil.rmtree(tmp, ignore_errors=True)

    passed = ok is True and ok2 is False and bo.classify_failure(reason2) == "dependency"
    print("\n" + ("✅ BUILD ENGINE VERIFIED" if passed else "❌ VERIFICATION FAILED"))
    return 0 if passed else 2


if __name__ == "__main__":
    sys.exit(main())
