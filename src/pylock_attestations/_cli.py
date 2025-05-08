"""The `pylock-attestations` entrypoint."""

import argparse
import dataclasses
import logging
import tomllib
from collections.abc import Mapping, Sequence
from http import HTTPStatus
from pathlib import Path
from typing import Any, NoReturn

import requests
import tomli_w
from packaging import pylock
from packaging.utils import Version, parse_sdist_filename, parse_wheel_filename
from pydantic import ValidationError
from pypi_attestations import Distribution, Provenance

from pylock_attestations import __version__

logging.basicConfig(format="%(message)s", datefmt="[%X]", handlers=[logging.StreamHandler()])
_logger = logging.getLogger(__name__)
_logger.setLevel(logging.INFO)


def _die(message: str) -> NoReturn:
    """Handle errors and terminate the program with an error code."""
    _logger.error(message)
    raise SystemExit(1)


def _download_provenance(name: str, version: Version, filename: str) -> Provenance | None:
    provenance_url = f"https://pypi.org/integrity/{name}/{version}/{filename}/provenance"
    response = requests.get(provenance_url, timeout=5)

    if response.status_code != HTTPStatus.OK.value:
        if response.status_code != HTTPStatus.NOT_FOUND.value:
            _logger.warning(
                "Unexpected error while downloading provenance file from PyPI, Integrity API "
                "returned status code: %s",
                {response.status_code},
            )
        return None

    try:
        return Provenance.model_validate_json(response.text)
    except ValidationError:
        _logger.warning(
            "Unexpected error while validating provenance downloaded from %s", provenance_url
        )
        return None


def _get_attestation_identities(package: pylock.Package) -> Sequence[Mapping[str, Any]] | None:
    """Use PyPI's integrity API to get a package's attestation identities."""
    filename, name, version, digest = None, None, None, None

    if package.sdist is not None and package.sdist.url is not None:
        url = package.sdist.url
        filename = url.split("/")[-1]
        name, version = parse_sdist_filename(filename)
        digest = package.sdist.hashes.get("sha256", None)
    elif package.wheels is not None:
        wheel_tuples = [(w.url, w.hashes) for w in package.wheels if w.url is not None]
        if wheel_tuples:
            url, digests = wheel_tuples[0]
            filename = url.split("/")[-1]
            name, version, _, _ = parse_wheel_filename(filename)
            digest = digests.get("sha256", None)

    if name is None or version is None or filename is None:
        return None

    provenance = _download_provenance(name=name, version=version, filename=filename)
    if provenance is None:
        return None

    # Verify the downloaded provenance against the digest in the lockfile
    if digest is not None:
        for bundle in provenance.attestation_bundles:
            for attestation in bundle.attestations:
                attestation.verify(bundle.publisher, Distribution(name=filename, digest=digest))

    return [bundle.publisher.dict(exclude_none=True) for bundle in provenance.attestation_bundles]


def _update_pylock_file(input_file: Path, output_file: Path) -> None:
    try:
        with input_file.open("rb") as f:
            pylock_dict = tomllib.load(f)
            pylock_data = pylock.from_dict(pylock_dict)
    except tomllib.TOMLDecodeError as e:
        _die(f"Invalid TOML in file '{input_file}': {e}")
    except pylock.PylockValidationError as e:
        _die(f"Error while parsing pylock file '{input_file}': {e}")

    new_packages = []
    modified = False
    for p in pylock_data.packages:
        new_package = p
        if p.attestation_identities is None:
            identities = _get_attestation_identities(p)
            if identities:
                new_package = dataclasses.replace(p, attestation_identities=identities)
                modified = True
                _logger.info("Package %s:\n\tAdding identities: %s", p.name, identities)

        new_packages.append(new_package)

    if not modified:
        return

    new_pylock_data = dataclasses.replace(pylock_data, packages=new_packages)
    try:
        output = tomli_w.dumps(new_pylock_data.to_dict())
    except (TypeError, ValueError) as e:
        _die(f"Error while converting new data to TOML: {e}")

    with output_file.open("w") as f:
        f.write(output)


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="pylock-attestations",
        description="Add attestation identities to a pylock.toml file",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )

    parser.add_argument(
        "-v",
        "--verbose",
        action="count",
        default=0,
        help="Run with additional debug logging; supply multiple times to increase verbosity",
    )

    parser.add_argument(
        "-V",
        "--version",
        action="version",
        version=f"pylock-attestations {__version__}",
    )
    parser.add_argument(
        "-i",
        "--input",
        type=Path,
        required=False,
        default="./pylock.toml",
        help="Path to pylock.toml file",
    )
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        required=False,
        help="Path to output file. If not specified, the input file is modified in-place.",
    )

    args: argparse.Namespace = parser.parse_args()

    if args.verbose >= 1:
        _logger.setLevel("DEBUG")
    if args.verbose >= 2:  # noqa: PLR2004
        logging.getLogger().setLevel("DEBUG")

    _logger.debug(args)

    if not args.input.exists():
        _die(f"File {args.input} not found")

    if args.output is not None and args.output.exists():
        _die(f"Output file {args.output} already exists")

    output_file = args.output if args.output is not None else args.input

    _update_pylock_file(args.input, output_file)
