"""Create or verify detached Ed25519 release manifests."""

from __future__ import annotations

import argparse
import os
import time

from Crypto.PublicKey import ECC

from common.update_security import build_manifest, sign_manifest, verify_manifest


def main() -> None:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)
    keygen = subparsers.add_parser("keygen")
    keygen.add_argument("--private", required=True)
    keygen.add_argument("--public", required=True)
    sign = subparsers.add_parser("sign")
    sign.add_argument("--package", required=True)
    sign.add_argument("--version", required=True)
    sign.add_argument("--private", required=True)
    verify = subparsers.add_parser("verify")
    verify.add_argument("--package", required=True)
    verify.add_argument("--manifest", required=True)
    verify.add_argument("--signature", required=True)
    verify.add_argument("--public", required=True)
    args = parser.parse_args()

    if args.command == "keygen":
        key = ECC.generate(curve="Ed25519")
        os.makedirs(os.path.dirname(os.path.abspath(args.private)), exist_ok=True)
        os.makedirs(os.path.dirname(os.path.abspath(args.public)), exist_ok=True)
        with open(args.private, "wt", encoding="ascii") as handle:
            handle.write(key.export_key(format="PEM"))
        with open(args.public, "wt", encoding="ascii") as handle:
            handle.write(key.public_key().export_key(format="PEM"))
        return

    if args.command == "sign":
        manifest = build_manifest(args.package, args.version, int(time.time()))
        with open(args.private, "rb") as handle:
            signature = sign_manifest(manifest, handle.read())
        with open(args.package + ".manifest.json", "wb") as handle:
            handle.write(manifest)
        with open(args.package + ".sig", "wt", encoding="ascii") as handle:
            handle.write(signature)
        return

    with open(args.manifest, "rb") as handle:
        manifest = handle.read()
    with open(args.signature, "rt", encoding="ascii") as handle:
        signature = handle.read().strip()
    with open(args.public, "rb") as handle:
        public_key = handle.read()
    verify_manifest(manifest, signature, public_key, package_path=args.package)


if __name__ == "__main__":
    main()
