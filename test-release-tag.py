#!/usr/bin/env python3
"""Offline tests for download-snapshots.py Phase C (release-tag downloads).

Run from repo root: ./venv/bin/python test-release-tag.py
Exit 0 = all pass. No network — sessions are mocked.
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
ds.time.sleep = lambda *_: None          # retry backoff would stall the suite

FAILS = 0


def check(label, got, want):
    global FAILS
    ok = got == want
    if not ok:
        FAILS += 1
    print(f"{'✅' if ok else '❌'} {label}: got {got!r} (want {want!r})")


class Resp:
    def __init__(self, status_code, content=b"", tag=None):
        self.status_code = status_code
        self.content = content
        self.headers = {}

    def json(self):
        return self._json

    _json = {}


def session(routes, expect_auth=False):
    """routes: {(kind, name): status}  kind in {'api','tags','heads'}"""
    calls = []

    class R(Resp):
        def __init__(self, status_code, content=b""):
            super().__init__(status_code, content)
            self._json = {}

    class S:
        headers = {"Authorization": "Bearer test"} if expect_auth else {
            "User-Agent": "t"}

        def get(self, url, **kw):
            calls.append(url)
            if "/releases/latest" in url:
                st = routes.get(("api", None), 404)
                r = R(st)
                if st == 200:
                    r._json = {"tag_name": "v9.9.9"}
                return r
            for kind, name in routes:
                if kind in ("tags", "heads") and url.endswith(f"/zip/refs/{kind}/{name}"):
                    return R(routes[(kind, name)], b"PK\x03\x04zipbytes")
            return R(404)

    return S(), calls


# --- resolve_release_tag ----------------------------------------------------
boom_calls = []

class Boom:
    headers = {}                       # no Authorization → must NOT call HTTP

    def get(self, *a, **kw):
        boom_calls.append(1)
        raise AssertionError("HTTP call attempted without token")

check("no token → no release lookup, no HTTP",
      ds.resolve_release_tag(Boom(), "a/b"), None)
check("no-token path made zero HTTP calls", len(boom_calls), 0)

s, calls = session({("api", None): 200}, expect_auth=True)
check("token + release → tag", ds.resolve_release_tag(s, "a/b"), "v9.9.9")
check("exactly one API call for release lookup", len(calls), 1)

s, _ = session({("api", None): 404}, expect_auth=True)
check("no releases (404) → None, not error", ds.resolve_release_tag(s, "a/b"), None)

s, _ = session({("api", None): 503}, expect_auth=True)
check("API 5xx after retries → None (fail-open)", ds.resolve_release_tag(s, "a/b"), None)

# --- fetch_zipball ------------------------------------------------------------
s, calls = session({("tags", "v1.0"): 200, ("heads", "main"): 200}, expect_auth=True)
resp, ref, kind = ds.fetch_zipball(s, "a/b", "v1.0", "main")
check("tag zip 200 → release-tag", (ref, kind), ("v1.0", "release-tag"))
check("branch never called when tag works",
      any("/refs/heads/" in u for u in calls), False)

s, calls = session({("tags", "v1.0"): 404, ("heads", "main"): 200}, expect_auth=True)
resp, ref, kind = ds.fetch_zipball(s, "a/b", "v1.0", "main")
check("tag zip 404 → branch fallback", (ref, kind), ("main", "branch"))
check("fallback hit the branch URL", any("/refs/heads/main" in u for u in calls), True)

s, _ = session({("heads", "main"): 200}, expect_auth=True)
resp, ref, kind = ds.fetch_zipball(s, "a/b", None, "main")
check("no tag → branch directly", (ref, kind), ("main", "branch"))

s, _ = session({("tags", "v1.0"): 404, ("heads", "main"): 404}, expect_auth=True)
resp, ref, kind = ds.fetch_zipball(s, "a/b", "v1.0", "main")
check("both 404 → branch ref + 404 for caller to record",
      (resp.status_code, ref, kind), (404, "main", "branch"))

print(f"\n{'🎉 ALL PASS' if FAILS == 0 else f'💥 {FAILS} FAILURES'}")
sys.exit(1 if FAILS else 0)
