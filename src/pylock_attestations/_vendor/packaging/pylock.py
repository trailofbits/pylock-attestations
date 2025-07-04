from __future__ import annotations

import dataclasses
import logging
import re
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import (
    TYPE_CHECKING,
    Any,
    Protocol,
    TypeVar,
)

from .markers import Marker
from .specifiers import SpecifierSet
from .utils import NormalizedName, is_normalized_name
from .version import Version

if TYPE_CHECKING:  # pragma: no cover
    if sys.version_info >= (3, 11):
        from typing import Self
    else:
        from typing_extensions import Self

__all__ = [
    "Package",
    "PackageArchive",
    "PackageDirectory",
    "PackageSdist",
    "PackageVcs",
    "PackageWheel",
    "Pylock",
    "PylockUnsupportedVersionError",
    "PylockValidationError",
    "is_valid_pylock_path",
]

T = TypeVar("T")


class FromMappingProtocol(Protocol):  # pragma: no cover
    @classmethod
    def _from_dict(cls, d: Mapping[str, Any]) -> Self: ...


FromMappingProtocolT = TypeVar("FromMappingProtocolT", bound=FromMappingProtocol)


class SingleArgConstructor(Protocol):  # pragma: no cover
    def __init__(self, value: Any) -> None: ...


SingleArgConstructorT = TypeVar("SingleArgConstructorT", bound=SingleArgConstructor)

PYLOCK_FILE_NAME_RE = re.compile(r"^pylock\.([^.]+)\.toml$")


def is_valid_pylock_path(path: Path) -> bool:
    return path.name == "pylock.toml" or bool(PYLOCK_FILE_NAME_RE.match(path.name))


def _toml_key(key: str) -> str:
    return key.replace("_", "-")


def _toml_value(key: str, value: Any) -> Any:
    if isinstance(value, (Version, Marker, SpecifierSet)):
        return str(value)
    if isinstance(value, Sequence) and key == "environments":
        return [str(v) for v in value]
    return value


def _toml_dict_factory(data: list[tuple[str, Any]]) -> dict[str, Any]:
    return {
        _toml_key(key): _toml_value(key, value)
        for key, value in data
        if value is not None
    }


def _get(d: Mapping[str, Any], expected_type: type[T], key: str) -> T | None:
    """Get value from dictionary and verify expected type."""
    value = d.get(key)
    if value is None:
        return None
    if not isinstance(value, expected_type):
        raise PylockValidationError(
            f"Unexpected type {type(value).__name__} "
            f"(expected {expected_type.__name__})",
            context=key,
        )
    return value


def _get_required(d: Mapping[str, Any], expected_type: type[T], key: str) -> T:
    """Get required value from dictionary and verify expected type."""
    value = _get(d, expected_type, key)
    if value is None:
        raise PylockRequiredKeyError(key)
    return value


def _get_sequence(
    d: Mapping[str, Any], expected_item_type: type[T], key: str
) -> Sequence[T] | None:
    """Get list value from dictionary and verify expected items type."""
    value = _get(d, Sequence, key)  # type: ignore[type-abstract]
    if value is None:
        return None
    for i, item in enumerate(value):
        if not isinstance(item, expected_item_type):
            raise PylockValidationError(
                f"Unexpected type {type(item).__name__} "
                f"(expected {expected_item_type.__name__})",
                context=f"{key}[{i}]",
            )
    return value


def _get_as(
    d: Mapping[str, Any],
    expected_type: type[T],
    target_type: type[SingleArgConstructorT],
    key: str,
) -> SingleArgConstructorT | None:
    """Get value from dictionary, verify expected type, convert to target type.

    This assumes the target_type constructor accepts the value.
    """
    value = _get(d, expected_type, key)
    if value is None:
        return None
    try:
        return target_type(value)
    except Exception as e:
        raise PylockValidationError(e, context=key) from e


def _get_required_as(
    d: Mapping[str, Any],
    expected_type: type[T],
    target_type: type[SingleArgConstructorT],
    key: str,
) -> SingleArgConstructorT:
    """Get required value from dictionary, verify expected type,
    convert to target type."""
    value = _get_as(d, expected_type, target_type, key)
    if value is None:
        raise PylockRequiredKeyError(key)
    return value


def _get_sequence_as(
    d: Mapping[str, Any],
    expected_item_type: type[T],
    target_item_type: type[SingleArgConstructorT],
    key: str,
) -> Sequence[SingleArgConstructorT] | None:
    """Get list value from dictionary and verify expected items type."""
    value = _get_sequence(d, expected_item_type, key)
    if value is None:
        return None
    result = []
    for i, item in enumerate(value):
        try:
            result.append(target_item_type(item))
        except Exception as e:
            raise PylockValidationError(e, context=f"{key}[{i}]") from e
    return result


def _get_object(
    d: Mapping[str, Any], target_type: type[FromMappingProtocolT], key: str
) -> FromMappingProtocolT | None:
    """Get dictionary value from dictionary and convert to dataclass."""
    value = _get(d, Mapping, key)  # type: ignore[type-abstract]
    if value is None:
        return None
    try:
        return target_type._from_dict(value)
    except Exception as e:
        raise PylockValidationError(e, context=key) from e


def _get_sequence_of_objects(
    d: Mapping[str, Any], target_item_type: type[FromMappingProtocolT], key: str
) -> Sequence[FromMappingProtocolT] | None:
    """Get list value from dictionary and convert items to dataclass."""
    value = _get(d, Sequence, key)  # type: ignore[type-abstract]
    if value is None:
        return None
    result = []
    for i, item in enumerate(value):
        if not isinstance(item, Mapping):
            raise PylockValidationError(
                f"Unexpected type {type(item).__name__} (expected Mapping)",
                context=f"{key}[{i}]",
            )
        try:
            result.append(target_item_type._from_dict(item))
        except Exception as e:
            raise PylockValidationError(e, context=f"{key}[{i}]") from e
    return result


def _get_required_list_of_objects(
    d: Mapping[str, Any], target_type: type[FromMappingProtocolT], key: str
) -> Sequence[FromMappingProtocolT]:
    """Get required list value from dictionary and convert items to dataclass."""
    result = _get_sequence_of_objects(d, target_type, key)
    if result is None:
        raise PylockRequiredKeyError(key)
    return result


def _validate_path_url(path: str | None, url: str | None) -> None:
    if not path and not url:
        raise PylockValidationError("path or url must be provided")


def _validate_hashes(hashes: Mapping[str, Any]) -> None:
    if not hashes:
        raise PylockValidationError("At least one hash must be provided")
    if not all(isinstance(hash, str) for hash in hashes.values()):
        raise PylockValidationError("Hash values must be strings")


class PylockValidationError(Exception):
    context: str | None = None
    message: str

    def __init__(
        self,
        cause: str | Exception,
        *,
        context: str | None = None,
    ) -> None:
        if isinstance(cause, PylockValidationError):
            if cause.context:
                self.context = (
                    f"{context}.{cause.context}" if context else cause.context
                )
            else:
                self.context = context
            self.message = cause.message
        else:
            self.context = context
            self.message = str(cause)

    def __str__(self) -> str:
        if self.context:
            return f"{self.message} in '{self.context}'"
        return self.message


class PylockRequiredKeyError(PylockValidationError):
    def __init__(self, key: str) -> None:
        super().__init__("Missing required value", context=key)


class PylockUnsupportedVersionError(PylockValidationError):
    pass


@dataclass(frozen=True, init=False)
class PackageVcs:
    type: str
    url: str | None  # = None
    path: str | None  # = None
    requested_revision: str | None  # = None
    commit_id: str
    subdirectory: str | None = None

    def __init__(
        self,
        *,
        type: str,
        url: str | None = None,
        path: str | None = None,
        requested_revision: str | None = None,
        commit_id: str,
        subdirectory: str | None = None,
    ) -> None:
        # In Python 3.10+ make dataclass kw_only=True and remove __init__
        object.__setattr__(self, "type", type)
        object.__setattr__(self, "url", url)
        object.__setattr__(self, "path", path)
        object.__setattr__(self, "requested_revision", requested_revision)
        object.__setattr__(self, "commit_id", commit_id)
        object.__setattr__(self, "subdirectory", subdirectory)
        # __post_init__ in Python 3.10+
        _validate_path_url(self.path, self.url)

    @classmethod
    def _from_dict(cls, d: Mapping[str, Any]) -> Self:
        return cls(
            type=_get_required(d, str, "type"),
            url=_get(d, str, "url"),
            path=_get(d, str, "path"),
            requested_revision=_get(d, str, "requested-revision"),
            commit_id=_get_required(d, str, "commit-id"),
            subdirectory=_get(d, str, "subdirectory"),
        )


@dataclass(frozen=True, init=False)
class PackageDirectory:
    path: str
    editable: bool | None = None
    subdirectory: str | None = None

    def __init__(
        self,
        *,
        path: str,
        editable: bool | None = None,
        subdirectory: str | None = None,
    ) -> None:
        # In Python 3.10+ make dataclass kw_only=True and remove __init__
        object.__setattr__(self, "path", path)
        object.__setattr__(self, "editable", editable)
        object.__setattr__(self, "subdirectory", subdirectory)

    @classmethod
    def _from_dict(cls, d: Mapping[str, Any]) -> Self:
        return cls(
            path=_get_required(d, str, "path"),
            editable=_get(d, bool, "editable"),
            subdirectory=_get(d, str, "subdirectory"),
        )


@dataclass(frozen=True, init=False)
class PackageArchive:
    url: str | None  # = None
    path: str | None  # = None
    size: int | None  # = None
    upload_time: datetime | None  # = None
    hashes: Mapping[str, str]
    subdirectory: str | None = None

    def __init__(
        self,
        *,
        hashes: Mapping[str, str],
        url: str | None = None,
        path: str | None = None,
        size: int | None = None,
        upload_time: datetime | None = None,
        subdirectory: str | None = None,
    ) -> None:
        # In Python 3.10+ make dataclass kw_only=True and remove __init__
        object.__setattr__(self, "url", url)
        object.__setattr__(self, "path", path)
        object.__setattr__(self, "size", size)
        object.__setattr__(self, "upload_time", upload_time)
        object.__setattr__(self, "hashes", hashes)
        object.__setattr__(self, "subdirectory", subdirectory)
        # __post_init__ in Python 3.10+
        _validate_path_url(self.path, self.url)
        _validate_hashes(self.hashes)

    @classmethod
    def _from_dict(cls, d: Mapping[str, Any]) -> Self:
        return cls(
            url=_get(d, str, "url"),
            path=_get(d, str, "path"),
            size=_get(d, int, "size"),
            upload_time=_get(d, datetime, "upload-time"),
            hashes=_get_required(d, Mapping, "hashes"),  # type: ignore[type-abstract]
            subdirectory=_get(d, str, "subdirectory"),
        )


@dataclass(frozen=True, init=False)
class PackageSdist:
    name: str | None  # = None
    upload_time: datetime | None  # = None
    url: str | None  # = None
    path: str | None  # = None
    size: int | None  # = None
    hashes: Mapping[str, str]

    def __init__(
        self,
        *,
        hashes: Mapping[str, str],
        name: str | None = None,
        upload_time: datetime | None = None,
        url: str | None = None,
        path: str | None = None,
        size: int | None = None,
    ) -> None:
        # In Python 3.10+ make dataclass kw_only=True and remove __init__
        object.__setattr__(self, "name", name)
        object.__setattr__(self, "upload_time", upload_time)
        object.__setattr__(self, "url", url)
        object.__setattr__(self, "path", path)
        object.__setattr__(self, "size", size)
        object.__setattr__(self, "hashes", hashes)
        # __post_init__ in Python 3.10+
        _validate_path_url(self.path, self.url)
        _validate_hashes(self.hashes)

    @classmethod
    def _from_dict(cls, d: Mapping[str, Any]) -> Self:
        return cls(
            name=_get(d, str, "name"),
            upload_time=_get(d, datetime, "upload-time"),
            url=_get(d, str, "url"),
            path=_get(d, str, "path"),
            size=_get(d, int, "size"),
            hashes=_get_required(d, Mapping, "hashes"),  # type: ignore[type-abstract]
        )


@dataclass(frozen=True, init=False)
class PackageWheel:
    name: str  # | None
    upload_time: datetime | None  # = None
    url: str | None  # = None
    path: str | None  # = None
    size: int | None  # = None
    hashes: Mapping[str, str]

    def __init__(
        self,
        *,
        hashes: Mapping[str, str],
        name: str | None = None,
        upload_time: datetime | None = None,
        url: str | None = None,
        path: str | None = None,
        size: int | None = None,
    ) -> None:
        # In Python 3.10+ make dataclass kw_only=True and remove __init__
        object.__setattr__(self, "name", name)
        object.__setattr__(self, "upload_time", upload_time)
        object.__setattr__(self, "url", url)
        object.__setattr__(self, "path", path)
        object.__setattr__(self, "size", size)
        object.__setattr__(self, "hashes", hashes)
        # __post_init__ in Python 3.10+
        _validate_path_url(self.path, self.url)
        _validate_hashes(self.hashes)

    @classmethod
    def _from_dict(cls, d: Mapping[str, Any]) -> Self:
        return cls(
            name=_get(d, str, "name"),
            upload_time=_get(d, datetime, "upload-time"),
            url=_get(d, str, "url"),
            path=_get(d, str, "path"),
            size=_get(d, int, "size"),
            hashes=_get_required(d, Mapping, "hashes"),  # type: ignore[type-abstract]
        )


@dataclass(frozen=True, init=False)
class Package:
    name: NormalizedName
    version: Version | None = None
    marker: Marker | None = None
    requires_python: SpecifierSet | None = None
    dependencies: Sequence[Mapping[str, Any]] | None = None
    vcs: PackageVcs | None = None
    directory: PackageDirectory | None = None
    archive: PackageArchive | None = None
    index: str | None = None
    sdist: PackageSdist | None = None
    wheels: Sequence[PackageWheel] | None = None
    attestation_identities: Sequence[Mapping[str, Any]] | None = None
    tool: Mapping[str, Any] | None = None

    def __init__(
        self,
        *,
        name: str,
        version: Version | None = None,
        marker: Marker | None = None,
        requires_python: SpecifierSet | None = None,
        dependencies: Sequence[Mapping[str, Any]] | None = None,
        vcs: PackageVcs | None = None,
        directory: PackageDirectory | None = None,
        archive: PackageArchive | None = None,
        index: str | None = None,
        sdist: PackageSdist | None = None,
        wheels: Sequence[PackageWheel] | None = None,
        attestation_identities: Sequence[Mapping[str, Any]] | None = None,
        tool: Mapping[str, Any] | None = None,
    ) -> None:
        # In Python 3.10+ make dataclass kw_only=True and remove __init__
        object.__setattr__(self, "name", name)
        object.__setattr__(self, "version", version)
        object.__setattr__(self, "marker", marker)
        object.__setattr__(self, "requires_python", requires_python)
        object.__setattr__(self, "dependencies", dependencies)
        object.__setattr__(self, "vcs", vcs)
        object.__setattr__(self, "directory", directory)
        object.__setattr__(self, "archive", archive)
        object.__setattr__(self, "index", index)
        object.__setattr__(self, "sdist", sdist)
        object.__setattr__(self, "wheels", wheels)
        object.__setattr__(self, "attestation_identities", attestation_identities)
        object.__setattr__(self, "tool", tool)
        # __post_init__ in Python 3.10+
        if not is_normalized_name(self.name):
            raise PylockValidationError(f"Package name {self.name!r} is not normalized")
        if self.sdist or self.wheels:
            if self.vcs or self.directory or self.archive:
                raise PylockValidationError(
                    "None of vcs, directory, archive "
                    "must be set if sdist or wheels are set"
                )
        else:
            # no sdist nor wheels
            if not (bool(self.vcs) ^ bool(self.directory) ^ bool(self.archive)):
                raise PylockValidationError(
                    "Exactly one of vcs, directory, archive must be set "
                    "if sdist and wheels are not set"
                )

    @classmethod
    def _from_dict(cls, d: Mapping[str, Any]) -> Self:
        package = cls(
            name=_get_required(d, str, "name"),
            version=_get_as(d, str, Version, "version"),
            requires_python=_get_as(d, str, SpecifierSet, "requires-python"),
            dependencies=_get_sequence(d, Mapping, "dependencies"),  # type: ignore[type-abstract]
            marker=_get_as(d, str, Marker, "marker"),
            vcs=_get_object(d, PackageVcs, "vcs"),
            directory=_get_object(d, PackageDirectory, "directory"),
            archive=_get_object(d, PackageArchive, "archive"),
            index=_get(d, str, "index"),
            sdist=_get_object(d, PackageSdist, "sdist"),
            wheels=_get_sequence_of_objects(d, PackageWheel, "wheels"),
            attestation_identities=_get_sequence(d, Mapping, "attestation-identities"),  # type: ignore[type-abstract]
            tool=_get(d, Mapping, "tool"),  # type: ignore[type-abstract]
        )
        return package

    @property
    def is_direct(self) -> bool:
        return not (self.sdist or self.wheels)


@dataclass(frozen=True, init=False)
class Pylock:
    lock_version: Version
    environments: Sequence[Marker] | None  # = None
    requires_python: SpecifierSet | None  # = None
    extras: Sequence[str] | None  # = None
    dependency_groups: Sequence[str] | None  # = None
    default_groups: Sequence[str] | None  # = None
    created_by: str
    packages: Sequence[Package]
    tool: Mapping[str, Any] | None = None

    def __init__(
        self,
        *,
        lock_version: Version,
        created_by: str,
        environments: Sequence[Marker] | None = None,
        requires_python: SpecifierSet | None = None,
        extras: Sequence[str] | None = None,
        dependency_groups: Sequence[str] | None = None,
        default_groups: Sequence[str] | None = None,
        packages: Sequence[Package],
        tool: Mapping[str, Any] | None = None,
    ) -> None:
        # In Python 3.10+ make dataclass kw_only=True and remove __init__
        object.__setattr__(self, "lock_version", lock_version)
        object.__setattr__(self, "environments", environments)
        object.__setattr__(self, "requires_python", requires_python)
        object.__setattr__(self, "extras", extras)
        object.__setattr__(self, "dependency_groups", dependency_groups)
        object.__setattr__(self, "default_groups", default_groups)
        object.__setattr__(self, "created_by", created_by)
        object.__setattr__(self, "packages", packages)
        object.__setattr__(self, "tool", tool)
        # __post_init__ in Python 3.10+
        if self.lock_version < Version("1") or self.lock_version >= Version("2"):
            raise PylockUnsupportedVersionError(
                f"pylock version {self.lock_version} is not supported"
            )
        if self.lock_version > Version("1.0"):
            logging.warning(
                "pylock minor version %s is not supported", self.lock_version
            )

    def to_dict(self) -> Mapping[str, Any]:
        return dataclasses.asdict(self, dict_factory=_toml_dict_factory)

    @classmethod
    def _from_dict(cls, d: Mapping[str, Any]) -> Self:
        return cls(
            lock_version=_get_required_as(d, str, Version, "lock-version"),
            environments=_get_sequence_as(d, str, Marker, "environments"),
            extras=_get_sequence(d, str, "extras"),
            dependency_groups=_get_sequence(d, str, "dependency-groups"),
            default_groups=_get_sequence(d, str, "default-groups"),
            created_by=_get_required(d, str, "created-by"),
            requires_python=_get_as(d, str, SpecifierSet, "requires-python"),
            packages=_get_required_list_of_objects(d, Package, "packages"),
            tool=_get(d, Mapping, "tool"),  # type: ignore[type-abstract]
        )

    @classmethod
    def from_dict(cls, d: Mapping[str, Any]) -> Self:
        return cls._from_dict(d)
