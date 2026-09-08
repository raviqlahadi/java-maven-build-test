#!/usr/bin/env python3
"""Offline gate tests for download-snapshots.py Phase A pre-download gate.

Run from repo root: ./venv/bin/python test-gate.py
Exit 0 = all pass. No network needed (fail-open checks use mock sessions).

Covers:
- JDK parsing via the SHARED orchestrator parser (parse_java_version_text):
  properties, maven-compiler-plugin <configuration>, 1.8 legacy format,
  the old substring bug's trap (dep version 11.0.2 must not select JDK 11),
  XML encoding declaration (bytes vs str), garbage input fails open.
- SNAPSHOT risk: snapshot parent, external-group snapshot dep,
  same-group (reactor sibling) snapshot dep must pass.
- Gate decisions: 404 -> pre-no-pom, any session exception -> proceed.
"""
import importlib.util
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


ds = _load("download_snapshots", os.path.join(HERE, "download-snapshots.py"))
orch = ds._orchestrator()

POMS = {
    "clean-jdk17": ("""<project xmlns="http://maven.apache.org/POM/4.0.0">
  <modelVersion>4.0.0</modelVersion>
  <groupId>org.acme</groupId><artifactId>app</artifactId><version>1.0</version>
  <properties><maven.compiler.release>17</maven.compiler.release></properties>
</project>""", None),

    "jdk25-unsupported": ("""<project xmlns="http://maven.apache.org/POM/4.0.0">
  <groupId>org.acme</groupId><artifactId>app</artifactId><version>1.0</version>
  <properties><java.version>25</java.version></properties>
</project>""", "pre-jdk-unsupported"),

    "compiler-plugin-22": ("""<project xmlns="http://maven.apache.org/POM/4.0.0">
  <groupId>org.acme</groupId><artifactId>app</artifactId><version>1.0</version>
  <build><plugins><plugin>
    <groupId>org.apache.maven.plugins</groupId><artifactId>maven-compiler-plugin</artifactId>
    <configuration><release>22</release></configuration>
  </plugin></plugins></build>
</project>""", "pre-jdk-unsupported"),

    "jdk21-passes": ("""<project xmlns="http://maven.apache.org/POM/4.0.0">
  <groupId>org.acme</groupId><artifactId>app</artifactId><version>1.0</version>
  <build><plugins><plugin>
    <artifactId>maven-compiler-plugin</artifactId>
    <configuration><release>21</release></configuration>
  </plugin></plugins></build>
</project>""", None),

    "snapshot-parent": ("""<project xmlns="http://maven.apache.org/POM/4.0.0">
  <parent>
    <groupId>com.corp</groupId><artifactId>corp-parent</artifactId><version>2.3-SNAPSHOT</version>
  </parent>
  <groupId>org.acme</groupId><artifactId>app</artifactId><version>1.0</version>
</project>""", "pre-snapshot"),

    "external-snapshot-dep": ("""<project xmlns="http://maven.apache.org/POM/4.0.0">
  <groupId>org.acme</groupId><artifactId>app</artifactId><version>1.0-SNAPSHOT</version>
  <dependencies>
    <dependency>
      <groupId>io.other</groupId><artifactId>lib</artifactId><version>3.1-SNAPSHOT</version>
    </dependency>
  </dependencies>
</project>""", "pre-snapshot"),

    "sibling-snapshot-dep-passes": ("""<project xmlns="http://maven.apache.org/POM/4.0.0">
  <groupId>org.acme</groupId><artifactId>app</artifactId><version>1.0-SNAPSHOT</version>
  <dependencies>
    <dependency>
      <groupId>org.acme</groupId><artifactId>app-core</artifactId><version>1.0-SNAPSHOT</version>
    </dependency>
  </dependencies>
</project>""", None),

    # The old POC substring trap: dep at version 11.0.2 on a Java 8 project
    "substring-trap-passes": ("""<project xmlns="http://maven.apache.org/POM/4.0.0">
  <groupId>org.acme</groupId><artifactId>app</artifactId><version>1.0</version>
  <properties><maven.compiler.source>1.8</maven.compiler.source></properties>
  <dependencies>
    <dependency><groupId>junit</groupId><artifactId>x</artifactId><version>11.0.2</version></dependency>
  </dependencies>
</project>""", None),

    # XML declaration + namespace, like every real pom from the CDN
    "xml-decl-realistic": ("""<?xml version="1.0" encoding="UTF-8"?>
<project xmlns="http://maven.apache.org/POM/4.0.0"
         xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">
  <modelVersion>4.0.0</modelVersion>
  <groupId>org.acme</groupId><artifactId>app</artifactId><version>1.0</version>
  <properties><maven.compiler.release>17</maven.compiler.release></properties>
</project>""", None),

    "garbage-fails-open": ("this is not xml <<<>>>", None),
    "empty-project": ("<project/>", None),
}


class BoomSession:
    def get(self, *a, **kw):
        raise RuntimeError("CDN down")


class NotFoundSession:
    class R:
        status_code = 404
        text = ""
        content = b""

    def get(self, *a, **kw):
        return self.R


def main():
    fails = 0
    for name, (pom, expected) in POMS.items():
        got = orch.parse_java_version_text(pom)
        major = orch._major(got) if got else None
        if major is not None and major > orch.MAX_MAPPED_JDK:
            verdict = "pre-jdk-unsupported"
        elif ds.pom_snapshot_risk(pom):
            verdict = "pre-snapshot"
        else:
            verdict = None
        ok = verdict == expected
        fails += 0 if ok else 1
        print(f"{'✅' if ok else '❌'} {name}: parsed={got!r} major={major} "
              f"→ {verdict} (want {expected})")

    reason = ds.pre_download_gate(BoomSession(), "x/y", "main")
    ok = reason is None
    fails += 0 if ok else 1
    print(f"{'✅' if ok else '❌'} gate fail-open on CDN exception → {reason} (want None)")

    reason = ds.pre_download_gate(NotFoundSession(), "x/y", "main")
    ok = reason == "pre-no-pom"
    fails += 0 if ok else 1
    print(f"{'✅' if ok else '❌'} gate 404 → {reason} (want pre-no-pom)")

    print(f"\n{'🎉 ALL PASS' if fails == 0 else f'💥 {fails} FAILURES'}")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
