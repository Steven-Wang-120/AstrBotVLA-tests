"""Explicit checkout bootstrap shared by relocated drivers and subprocesses."""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def bootstrap():
    # Strip only harness-specific options before legacy driver argparse runs.
    # Inherit them via PYTHONPATH/env for multiprocessing and child CLIs.
    options = {"--ex-checkout": "ASTRBOTEX_TEST_CHECKOUT",
               "--aeb-checkout": "ASTRBOTEX_TEST_AEB_CHECKOUT",
               "--host-checkout": "ASTRBOTVLA_TEST_HOST_CHECKOUT",
               "--artifacts-dir": "ASTRBOTVLA_ARTIFACTS",
               "--cc-switch-db": "ASTRBOTVLA_CC_SWITCH_DB",
               "--jev-key-file": "ASTRBOTVLA_JEV_KEY_FILE"}
    remaining = [sys.argv[0]]
    i = 1
    while i < len(sys.argv):
        arg = sys.argv[i]
        if arg == "--":
            remaining.extend(sys.argv[i:])
            break
        key, equal, value = arg.partition("=")
        if key in options:
            if not equal:
                i += 1
                if i >= len(sys.argv):
                    raise ValueError(key + " requires an explicit path")
                value = sys.argv[i]
            if not value or value.startswith('--'):
                raise ValueError(key + " requires an explicit path")
            os.environ[options[key]] = str(Path(value).resolve())
        else:
            remaining.append(arg)
        i += 1
    sys.argv[:] = remaining
    for env, marker in (("ASTRBOTEX_TEST_CHECKOUT", "astrbot_ex/core/api_server.py"),
                        ("ASTRBOTEX_TEST_AEB_CHECKOUT", "astrbot_plugin_astrbotex_interaction/main.py"),
                        ("ASTRBOTVLA_TEST_HOST_CHECKOUT", "astrbot/core/agent/message.py")):
        value = os.environ.get(env)
        if value:
            root = Path(value)
            if not root.is_absolute() or not (root / marker).is_file():
                raise ValueError(env + " must identify an absolute real checkout")
            if str(root) not in sys.path:
                sys.path.append(str(root))
    # Existing EX tests are a package in the split repo; do not let site-packages
    # or the validation namespace take its place.
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    paths = [str(ROOT)] + [os.environ[e] for e in
             ("ASTRBOTEX_TEST_CHECKOUT", "ASTRBOTEX_TEST_AEB_CHECKOUT", "ASTRBOTVLA_TEST_HOST_CHECKOUT") if os.environ.get(e)]
    os.environ["PYTHONPATH"] = os.pathsep.join(dict.fromkeys(paths +
                                   os.environ.get("PYTHONPATH", "").split(os.pathsep)))
    os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
    sys.dont_write_bytecode = True
    artifacts = Path(os.environ.get("ASTRBOTVLA_ARTIFACTS", str(ROOT / "artifacts"))).resolve()
    for env in ("ASTRBOTEX_TEST_CHECKOUT", "ASTRBOTEX_TEST_AEB_CHECKOUT", "ASTRBOTVLA_TEST_HOST_CHECKOUT"):
        if os.environ.get(env) and artifacts.is_relative_to(Path(os.environ[env]).resolve()):
            raise ValueError("artifacts must be outside functional checkouts")
    if artifacts.is_relative_to(ROOT) and not artifacts.is_relative_to(ROOT / "artifacts"):
        raise ValueError("in-repository outputs must be under ignored artifacts/")
    os.environ["ASTRBOTVLA_ARTIFACTS"] = str(artifacts)


def ex_root():
    value = os.environ.get("ASTRBOTEX_TEST_CHECKOUT")
    if not value:
        raise ValueError("explicit --ex-checkout or ASTRBOTEX_TEST_CHECKOUT is required")
    return Path(value).resolve()


def source_path(name):
    if name.startswith("astrbot_ex/") or name in ("docs/DECISION-CONTRACT.md", "docs/DECISION-API.md"):
        return ex_root() / name
    return ROOT / name


def artifacts_path(name):
    return Path(os.environ.get("ASTRBOTVLA_ARTIFACTS", str(ROOT / "artifacts"))) / name


def explicit_secret_path(env):
    value = os.environ.get(env)
    return Path(value) if value else None
