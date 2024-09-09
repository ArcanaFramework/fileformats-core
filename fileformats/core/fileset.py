import os
from copy import copy
import struct
from enum import Enum, IntEnum
from warnings import warn
import inspect
import tempfile
from collections import Counter
import typing as ty
import shutil
from operator import itemgetter
import itertools
import functools
from pathlib import Path
import hashlib
import logging
from fileformats.core.typing import Self
from .utils import (
    classproperty,
    mtime_cached_property,
    fspaths_converter,
    describe_task,
    matching_source,
    import_extras_module,
)
from .typing import FspathsInputType, CryptoMethod, PathType
from .sampling import SampleFileGenerator
from .identification import (
    to_mime_format_name,
    IANA_MIME_TYPE_REGISTRIES,
)
from .classifier import Classifier
from .exceptions import (
    FormatMismatchError,
    UnconstrainedExtensionException,
    FormatConversionError,
    UnsatisfiableCopyModeError,
    FormatDefinitionError,
    FileFormatsExtrasError,
    FileFormatsExtrasPkgUninstalledError,
    FileFormatsExtrasPkgNotCheckedError,
)
from .datatype import DataType
from .extras import extra
from .fs_mount_identifier import FsMountIdentifier

if ty.TYPE_CHECKING:
    from pydra.engine.task import TaskBase
    from .converter_helpers import ConverterSpec


FILE_CHUNK_LEN_DEFAULT = 8192


logger = logging.getLogger("fileformats")


class FileSet(DataType):
    """
    The base class for all format types within the fileformats package. A generic
    representation of a collection of files related to a single data resource. A
    file-set can be a single file or directory or a collection thereof, such as a
    primary file with a "side-car" header.

    Parameters
    ----------
    fspaths : set[Path]
        a set of file-system paths pointing to all the resources in the file-set
    metadata : dict[str, Any]
        metadata associated with the file-set, typically lazily loaded via `read_metadata`
        extra hook but can be provided directly at the time of instantiation
    metadata_keys : list[str]
        the keys of the metadata to load when the `metadata` property is called. Provided
        to allow for selective loading of metadata fields for performance reasons.
    """

    # Class attributes

    # differentiate between Field and other DataType classes
    is_fileset = True

    # File extensions associated with file format
    ext: ty.Union[str, None] = None
    alternate_exts: ty.Tuple[ty.Optional[str], ...] = ()

    # to be overridden in subclasses
    # Explicitly set the Internet Assigned Numbers Authority (https://iana_mime.org) MIME
    # type to None for any base classes that should not correspond to a MIME or MIME-like
    # type.
    iana_mime: ty.Optional[str] = None

    # Member attributes
    fspaths: ty.FrozenSet[Path]
    _explicit_metadata: ty.Optional[ty.Mapping[str, ty.Any]]
    _metadata_keys: ty.Optional[ty.Collection[str]]

    def __init__(
        self,
        fspaths: FspathsInputType,
        metadata: ty.Optional[ty.Dict[str, ty.Any]] = None,
        metadata_keys: ty.Optional[ty.Collection[str]] = None,
    ):
        self._explicit_metadata = metadata
        self._metadata_keys = metadata_keys
        self._validate_class()
        self.fspaths = fspaths_converter(fspaths)
        self._validate_fspaths()
        self._additional_fspaths()
        if metadata and not isinstance(metadata, dict):
            raise TypeError(
                f"Fileset metadata value needs to be None or dict, not {metadata} ({self})"
            )
        self._validate_properties()

    def _validate_fspaths(self) -> None:
        if not self.fspaths:
            raise ValueError(f"No file-system paths provided to {self}")
        missing = [p for p in self.fspaths if not p or not p.exists()]
        if missing:
            missing_str = "\n".join(str(p) for p in missing)
            msg = (
                f"The following file system paths provided to {type(self)} do not "
                f"exist:\n\n{missing_str}\n\n"
            )
            present_parents = set()
            for fspath in missing:
                if fspath:
                    if fspath.parent.exists():
                        present_parents.add(fspath.parent)
            for parent in present_parents:
                msg += (
                    f"\n\nFiles in the present parent directory '{str(parent)}' are:\n"
                )
                msg += "\n".join(str(p) for p in parent.iterdir())
            raise FileNotFoundError(msg)

    def _validate_class(self) -> ty.Union[bool, None]:
        """Check that the class has been correctly defined"""
        if self._valid_class:
            return True
        type(self)._valid_class = True
        module_name = type(self).__module__
        if (
            not module_name.startswith("fileformats")
            and not isinstance(self, MockMixin)
            and not module_name.split(".")[-1].startswith("test_")
        ):
            raise FormatDefinitionError(
                f"FileSet class {type(self).__name__} should be defined in the "
                f"fileformats namespace not {module_name} unless it is in a test module"
            )
        return None  # Continue with subclass validation

    def _additional_fspaths(self) -> None:
        """Additional checks to be performed on the file-system paths provided to the"""

    def _validate_properties(self) -> None:
        # Check required properties don't raise errors
        for prop_name in self.required_properties():
            getattr(self, prop_name)

    def __eq__(self, other: object) -> bool:
        return (
            isinstance(other, FileSet)
            and type(self) is type(other)
            and self.fspaths == other.fspaths
        )

    def __ne__(self, other: object) -> bool:
        return not self.__eq__(other)

    def __hash__(self) -> int:
        tp = type(self)
        return hash((tp.__module__, tp.__name__, self.fspaths))

    def __repr__(self) -> str:
        return f"{self.type_name}('" + "', '".join(str(p) for p in self.fspaths) + "')"

    @property
    def parent(self) -> Path:
        "A common parent directory for all the top-level paths in the file-set"
        return Path(os.path.commonpath([p.parent for p in self.fspaths]))

    @property
    def relative_fspaths(self) -> ty.Iterator[Path]:
        "Paths for all top-level paths in the file-set relative to the common parent directory"
        return (p.relative_to(self.parent) for p in self.fspaths)

    @property
    def mtimes(self) -> ty.Tuple[ty.Tuple[str, float], ...]:
        """Modification times of all fspaths in the file-set

        Returns
        -------
        tuple[tuple[str, float], ...]
            a tuple of tuples containing the file paths and the modification time sorted
            by the file path
        """
        return tuple((str(p), p.stat().st_mtime) for p in sorted(self.fspaths))

    @classproperty
    def mime_type(cls) -> str:
        """Generates a MIME type (IANA) identifier from a format class. If an official
        IANA MIME type doesn't exist it will create one in the in the MIME style, e.g.

            fileformats.text.Plain to "text/plain"

            fileformats.image.TiffFx to "image/tiff-fx"

            fileformats.mynamespace.MyFormat to "application/x-my-format

        Returns
        -------
        str
            the MIME type corresponding to the class
        """
        try:
            mime_type = cls.__dict__["iana_mime"]
            assert isinstance(mime_type, str)
            return mime_type
        except KeyError:
            assert isinstance(cls, type)
            format_name = to_mime_format_name(cls.__name__)
            return f"application/x-{format_name}"

    @classproperty
    def strext(cls) -> str:
        """Return extension that is guaranteed to be a string (i.e. not None)"""
        return cls.ext if cls.ext is not None else ""

    @classproperty
    def unconstrained(cls) -> bool:
        """Whether the file-format is unconstrained by extension, magic number or another
        constraint"""
        return not list(cls.required_properties())

    @classproperty
    def possible_exts(cls) -> ty.List[ty.Optional[str]]:
        """All possible extensions of the file format"""
        possible = [cls.ext]
        try:
            possible.extend(cls.alternate_exts)
        except AttributeError:
            pass
        return possible

    @mtime_cached_property
    def metadata(self) -> ty.Mapping[str, ty.Any]:
        """Lazily load metadata from `read_metadata` extra if implemented, returning an
        empty metadata array if not"""
        if self._explicit_metadata is not None:
            return self._explicit_metadata
        try:
            metadata = self.read_metadata(selected_keys=self._metadata_keys)
        except FileFormatsExtrasPkgUninstalledError:
            raise
        except FileFormatsExtrasPkgNotCheckedError as e:
            logger.warning(str(e))
            metadata = {}
        except FileFormatsExtrasError:
            metadata = {}
        return metadata

    @extra
    def read_metadata(
        self, selected_keys: ty.Optional[ty.Collection[str]] = None
    ) -> ty.Mapping[str, ty.Any]:
        """Reads any metadata associated with the fileset and returns it as a dict

        Parameters
        ----------
        selected_keys : Sequence[str], optional
            selected keys to load instead of loading the complete metadata set

        Returns
        -------
        metadata : Mapping[str, Any]
            a mapping from names of the metadata fields to their values
        """
        raise NotImplementedError

    @classmethod
    def required_properties(cls) -> ty.Tuple[str, ...]:
        """Find all properties required to treat file-set as being in the format specified
        by the class

        Returns
        -------
        tuple[str, ...]
            a tuple containing all the properties names defined outside of core and
            generic classes
        """
        required_props = cls.__dict__.get("_required_props")
        if required_props is not None:
            assert isinstance(required_props, tuple) and all(
                isinstance(p, str) for p in required_props
            )
            return required_props  # return cached value
        required_props = set()
        for base in cls.__mro__:
            mod_name = base.__module__
            mod_parts = mod_name.split(".")
            if mod_name.startswith("fileformats"):
                subpkg = mod_parts[1]
            elif mod_name.split(".")[-1].startswith("test_"):
                subpkg = None
            else:
                continue
            if subpkg in ("core", "generic") and mod_parts[-1] != "mixin":
                base_name = base.__name__
                if base_name == "FsObject":
                    required_props.add("fspath")
                elif base_name in ("Directory", "TypedSet"):
                    required_props.add("_validate_contents")
            else:
                required_props.update(
                    name
                    for name, attr in base.__dict__.items()
                    if (not name.startswith("__") and isinstance(attr, property))
                )
        required_props = tuple(required_props)
        cls._required_props = required_props
        return required_props

    def required_paths(self) -> ty.FrozenSet[Path]:
        """Returns all fspaths that are required for the format"""
        required = set()
        for prop_name in self.required_properties():
            prop = getattr(self, prop_name)
            paths = []
            if hasattr(prop, "required_paths"):
                paths = prop.required_paths()
            elif isinstance(prop, os.PathLike):
                paths = [Path(prop)]
            elif isinstance(prop, ty.Iterable):
                for p in prop:
                    if isinstance(p, os.PathLike):
                        paths.append(Path(p))
            for path in paths:
                if path in self.fspaths:
                    required.add(path)
        return frozenset(required)

    def nested_filesets(self) -> ty.List["FileSet"]:
        """Returns all nested filesets that are required for the format

        Returns
        ------
        fileset : list[FileSet]
            a fileset that is nested within the broader fileset
        """
        nested = []
        for prop_name in sorted(self.required_properties()):
            prop = getattr(self, prop_name)
            if isinstance(prop, FileSet):
                nested.append(prop)
                nested.extend(prop.nested_filesets())
        return nested

    def trim_paths(self) -> Self:
        """Trims paths in fspaths to only those that are "required" by the format class
        i.e. returned by a required property"""
        self.fspaths = fspaths_converter(self.required_paths())
        return self

    def select_by_ext(self, fileformat: ty.Optional[ty.Type["FileSet"]] = None) -> Path:
        """Selects a single path from a set of file-system paths based on the file
        extension

        Parameters
        ----------
        fileformat : type
            the format class of the path to select

        Returns
        -------
        Path
            the selected file-system path that matches the extension

        Raises
        ------
        FormatMismatchError
            if more than one paths matches the extension
        """
        if fileformat is None:
            fileformat = type(self)
        exts = [fileformat.ext]
        try:
            exts.extend(fileformat.alternate_exts)
        except AttributeError:
            pass
        matches = self.matching_exts(self.fspaths, exts)
        if not matches:
            paths_str = ", ".join(str(p) for p in self.fspaths)
            raise FormatMismatchError(
                f"No matching files with extensions in {exts} in file set {paths_str}"
            )
        elif len(matches) > 1:
            matches_str = ", ".join(str(p) for p in matches)
            raise FormatMismatchError(
                f"Multiple files with {exts} extensions found in : {matches_str}"
            )
        return matches[0]

    @classmethod
    def matching_exts(
        cls,
        fspaths: ty.Collection[Path],
        exts: ty.Optional[ty.List[ty.Optional[str]]] = None,
    ) -> ty.List[Path]:
        """Returns the paths out of the candidates provided that matches the
        given extension (by default the extension of the class)

        Parameters
        ----------
        fspaths: list[Path]
            The paths to select from
        ext: list[str], optional
            the extensions to match, by default the primary and alternate extensions of
            the class

        Returns
        -------
        list[Path]
            the matching paths

        Raises
        ------
        FileFormatError
            When no paths match or more than one path matches the given extension"""
        if isinstance(fspaths, (str, Path)):
            fspaths = [fspaths]
        if exts is None:
            if cls.ext is None:
                return list(fspaths)
            exts = cls.possible_exts
        return [
            p for p in fspaths if any(e is None or str(p).endswith(e) for e in exts)
        ]

    @classmethod
    def convert(
        cls,
        fileset: "FileSet",
        plugin: str = "serial",
        task_name: ty.Optional[str] = None,
        **kwargs: ty.Any,
    ) -> Self:
        """Convert a given file-set into the format specified by the class

        Parameters
        ----------
        fileset : FileSet
            the file-set object to convert
        plugin : str
            the "execution plugin" used to run the conversion task
        task_name : str
            the name given to the converter task
        **kwargs
            args to pass to the conversion process

        Returns
        -------
        FileSet
            the file-set converted into the type of the current class
        """
        if task_name is None:
            task_name = f"{type(fileset).__name__}_to_{cls.__name__}_{id(fileset)}"
        # Make unique, yet somewhat recognisable task name
        task = cls.get_converter(source_format=type(fileset), name=task_name, **kwargs)
        result = task(in_file=fileset, plugin=plugin)
        out_file = result.output.out_file
        if not isinstance(out_file, cls):
            out_file = cls(out_file)
        return out_file  # type: ignore

    @classmethod
    def get_converter(
        cls,
        source_format: ty.Type[DataType],
        name: str = "converter",
        **kwargs: ty.Any,
    ) -> "TaskBase":
        """Get a converter that converts from the source format type
        into the format specified by the class

        Parameters
        ----------
        source_format : type
            the format to convert from
        name : str
            the name given to the converter task
        **kwargs
            passed on to the task init method to customise the conversion

        Returns
        -------
        pydra.engine.TaskBase or None
            a pydra task or workflow that performs the conversion, or None if no
            conversion is required

        Raises
        ------
        FileFormatConversionError
            no converters found between source and dest format
        FileFormatConversionError
            ambiguous (i.e. more than one) converters found between source and dest format
        """
        if issubclass(source_format, cls):
            return None
        # trigger loading of standard converters for target format
        converters = cls.get_converters_dict()
        try:
            unclassified = source_format.unclassified  # type: ignore
        except AttributeError:
            import_extras_module(source_format)
        else:
            import_extras_module(unclassified)
        try:
            converter_spec = converters[source_format]
        except KeyError:
            # If no direct mapping check for mapping from source super types and wildcard
            # matches
            available_converters = cls.get_converter_specs(source_format)
            if len(available_converters) > 1:
                # FIXME: Hack to avoid situation where multiple converters get added but are identical
                if all(a == available_converters[0] for a in available_converters[1:]):
                    available_converters = [available_converters[0]]
                else:
                    available_str = "\n".join(
                        describe_task(a.task) for a in available_converters
                    )
                    raise FormatConversionError(
                        f"Ambiguous converters found between '{cls.mime_like}' and "
                        f"'{source_format.mime_like}':\n{available_str}"
                    ) from None
            if not available_converters:
                msg = (
                    f"Could not find converter between '{source_format.mime_like}' and "
                    f"'{cls.mime_like}' formats"
                )
                extras_mod = import_extras_module(cls)
                if not extras_mod.imported:
                    msg += (
                        f'. Was not able to import "extras" module, {extras_mod.pkg}, '
                        f"you may want to try installing the '{extras_mod.pypi}' package "
                        f"from PyPI (e.g. pip install {extras_mod.pypi}) or check it isn't broken"
                    )
                raise FormatConversionError(msg) from None
            converter_spec = available_converters[0]
            # Store mapping for future reference
            converters[source_format] = converter_spec
        if kwargs:
            # Merge kwargs provided to get_conveter with stored kwargs
            conv_kwargs = copy(converter_spec.args)
            conv_kwargs.update(kwargs)
        else:
            conv_kwargs = converter_spec.args
        return converter_spec.task(name=name, **conv_kwargs)

    @classmethod
    def get_converters_dict(
        cls, klass: ty.Optional[ty.Type[DataType]] = None
    ) -> ty.Dict[ty.Type[DataType], "ConverterSpec"]:
        # Only access converters to the specific class, not superclasses (which may not
        # be able to convert to the specific type)
        if klass is None:
            klass = cls
        # import related extras module for the target class
        import_extras_module(klass)
        converters_dict: ty.Dict[ty.Type[DataType], "ConverterSpec"]
        try:
            converters_dict = klass.__dict__["converters"]
        except KeyError:
            converters_dict = klass.converters = {}
        return converters_dict

    @classmethod
    def get_converter_specs(cls, source_format: type) -> ty.List["ConverterSpec"]:
        """Search the registered converters to find any matches and return list of
        task and associated key-word args to perform the conversion between source and
        target formats

        Parameters
        ----------
        source_format : type(FileSet)
            the source format to convert from

        Returns
        -------
        available : list[tuple[TaskBase, dict[str, Any]]]
            list of available converters between the source and target formats
        """
        from .converter_helpers import SubtypeVar

        converters_dict = cls.get_converters_dict()
        available = []
        for src_frmt, converter in converters_dict.items():
            # Ignore converters with wildcards at this point
            if not converter.classifiers:
                if issubclass(source_format, src_frmt):
                    available.append(converter)
        if not available and hasattr(source_format, "unclassified"):
            available = SubtypeVar.get_converter_specs(source_format, target_format=cls)
        return available

    @classmethod
    def register_converter(
        cls,
        source_format: ty.Type["FileSet"],
        converter_spec: "ConverterSpec",
    ) -> None:
        """Registers a converter task within a class attribute. Called by the
        @fileformats.core.converter decorator.

        Parameters
        ----------
        source_format : type
            the source format to register a converter from
        converter_spec
            a tuple consisting of a `task_spec` callable that resolves to a Pydra task
            and a dictionary of keyword arguments to be passed to the task spec at
            initialisation time

        Raises
        ------
        FormatConversionError
            if there is already a converter registered between the two types
        """
        converters_dict = cls.get_converters_dict()
        # If no converters are loaded, attempt to load from the standard location
        if source_format in converters_dict:
            prev_spec = cls.converters[source_format]
            # task, task_kwargs = converter_spec
            # prev_task, prev_kwargs = prev_tuple
            if (
                matching_source(converter_spec.task, prev_spec.task)
                and converter_spec.args == prev_spec.args
            ):
                logger.warning(
                    "Ignoring duplicate registrations of the same converter %s",
                    describe_task(converter_spec.task),
                )
                return  # actually the same task but just imported twice for some reason
            raise FormatConversionError(
                f"Cannot register converter from {source_format.__name__} "
                f"to {cls.__name__}, {describe_task(converter_spec.task)}, because there is already "
                f"one registered from {describe_task(prev_spec.task)}:"
                f"\n\n{inspect.getsource(converter_spec.task)}\n\n"
                f"and{inspect.getsource(prev_spec.task)}\n\n"
            )
        converters_dict[source_format] = converter_spec

    @classproperty
    def all_formats(cls) -> ty.Set[ty.Type["FileSet"]]:
        """Iterate over all FileSet formats in fileformats.* namespaces"""
        if cls._all_formats is None:
            cls._all_formats = set(
                f
                for f in FileSet.subclasses()
                if (f.__dict__.get("iana_mime", True) and not inspect.isabstract(f))
            )
        return cls._all_formats

    @classproperty
    def standard_formats(cls) -> ty.Iterable[ty.Type["FileSet"]]:
        """Iterate over all formats in the standard fileformats.* namespaces"""
        return (f for f in cls.all_formats if f.namespace in IANA_MIME_TYPE_REGISTRIES)

    @classproperty
    def formats_by_iana_mime(cls) -> ty.Dict[str, ty.Type["FileSet"]]:
        """a dictionary containing all formats by their IANA MIME type (if applicable)"""
        if cls._formats_by_iana_mime is None:
            cls._formats_by_iana_mime = {
                f.iana_mime: f  # type: ignore[misc]
                for f in FileSet.all_formats
                if f.__dict__.get("iana_mime") is not None
            }
        return cls._formats_by_iana_mime

    @classproperty
    def formats_by_name(cls) -> ty.Dict[str, ty.Set[ty.Type["FileSet"]]]:
        """a dictionary containing lists of formats by their translated class names,
        i.e. their can be more than one format with the same translated name"""
        if cls._formats_by_name is None:
            cls._formats_by_name = {
                k: set(v for _, v in g)
                for k, g in itertools.groupby(
                    sorted(
                        (
                            (to_mime_format_name(f.__name__), f)
                            for f in FileSet.all_formats
                            if f.__dict__.get("iana_mime") is None
                        ),
                        key=itemgetter(0),
                    ),
                    key=itemgetter(0),
                )
            }
        return cls._formats_by_name

    @property
    def all_file_paths(self) -> ty.Iterable[Path]:
        """Paths of all files within the fileset"""
        for fspath in self.fspaths:
            if fspath.is_file():
                yield fspath
            else:
                for dpath, _, file_paths in os.walk(fspath):
                    for file_path in file_paths:
                        yield Path(dpath) / file_path

    def byte_chunks(
        self,
        mtime: bool = False,
        chunk_len: int = FILE_CHUNK_LEN_DEFAULT,
        relative_to: ty.Optional[Path] = None,
        ignore_hidden_files: bool = False,
        ignore_hidden_dirs: bool = False,
    ) -> ty.Generator[ty.Tuple[str, ty.Iterator[bytes]], None, None]:
        """Yields relative paths for all files within the file-set along with iterators
        over their byte-contents. To be used when generating hashes for the file set.

        Parameters
        ----------
        mtime : bool, optional
            instead of iterating over the entire contents of the file-set, simply yield
            a bytes repr of the last modification time.
        chunk_len : int, optional
            the chunk length to break up the file and calculate the hash over, by default 8192
        relative_to : Path, optional
            the base path by which to record the file system paths in the dictionary keys
            to, by default None
        ignore_hidden_files : bool
            whether to ignore hidden files within nested directories (i.e. those
            starting with '.')
        ignore_hidden_dirs : bool
            whether to ignore hidden directories within nested directories (i.e. those
            starting with '.')

        Yields
        -------
        rel_path: str
            relative path to either 'relative_to' arg or common base path for each file
            in the file set
        byte_iter : Generator[bytes]
            an iterator over the bytes contents of the file, chunked into 'chunk_len'
            chunks
        """
        # If "relative_to" is not provided, get the common path between
        if relative_to is None:
            relative_to = Path(os.path.commonpath(list(self.fspaths)))
            if all(p.is_file() and p.parent == relative_to for p in self.fspaths):
                relative_to /= os.path.commonprefix(
                    [p.name for p in self.fspaths]
                ).rstrip(".")
        # yield the absolute base path if using mtimes instead of contents
        if mtime:
            yield ("<base-path>", iter([str(relative_to.absolute()).encode()]))
        relative_to_str = str(relative_to)
        if relative_to.is_dir() and not relative_to_str.endswith(os.path.sep):
            relative_to_str += os.path.sep

        if mtime:

            def chunk_file(fspath: Path) -> ty.Iterator[bytes]:
                """Yields a byte representation of the last modified time for the file"""
                yield bytes(struct.pack("<d", os.stat(fspath).st_mtime))

        else:

            def chunk_file(fspath: Path) -> ty.Iterator[bytes]:
                """Yields the contents of the file in byte chunks"""
                if not fspath.is_file():
                    assert fspath.is_symlink()  # broken symlink
                    yield b"\x00"
                else:
                    with open(fspath, "rb") as fp:
                        for chunk in iter(functools.partial(fp.read, chunk_len), b""):
                            yield chunk

        def chunk_dir(fspath: Path) -> ty.Iterator[ty.Tuple[str, ty.Iterator[bytes]]]:
            fspath = Path(fspath)
            for dpath_str, _, filenames in sorted(os.walk(fspath)):
                # Sort in-place to guarantee order.
                filenames.sort()
                dpath = Path(dpath_str)
                if (
                    ignore_hidden_dirs
                    and dpath.name.startswith(".")
                    and dpath != fspath
                ):
                    continue
                for filename in filenames:
                    if ignore_hidden_files and filename.startswith("."):
                        continue
                    yield (
                        str((dpath / filename).relative_to(relative_to_str)),
                        chunk_file(dpath / filename),
                    )

        for key, fspath in sorted(
            ((str(p)[len(relative_to_str) :], p) for p in self.fspaths),
            key=itemgetter(0),
        ):
            if fspath.is_dir():
                yield from chunk_dir(fspath)
            else:
                yield (key, chunk_file(fspath))

    def hash(
        self,
        crypto: CryptoMethod = None,
        mtime: bool = False,
        chunk_len: int = FILE_CHUNK_LEN_DEFAULT,
        relative_to: ty.Optional[Path] = None,
        ignore_hidden_files: bool = False,
        ignore_hidden_dirs: bool = False,
    ) -> str:
        """Calculate a unique hash for the file-set based on the relative paths and
        contents of its constituent files

        Parameters
        ----------
        crypto : function, optional
            the cryptography method used to hash the files, by default hashlib.sha256
        **kwargs
            keyword args passed directly through to the ``hash_dir`` function

        Returns
        -------
        hash : str
            unique hash for the file-set
        """
        if crypto is None:
            crypto = hashlib.sha256
        crytpo_obj = crypto()
        for path, bytes_iter in self.byte_chunks(
            mtime=mtime,
            chunk_len=chunk_len,
            relative_to=relative_to,
            ignore_hidden_files=ignore_hidden_files,
            ignore_hidden_dirs=ignore_hidden_dirs,
        ):
            crytpo_obj.update(path.encode())
            for bytes_str in bytes_iter:
                crytpo_obj.update(bytes_str)
        digest: str = crytpo_obj.hexdigest()
        return digest

    def hash_files(
        self,
        crypto: CryptoMethod = None,  # s
        mtime: bool = False,
        chunk_len: int = FILE_CHUNK_LEN_DEFAULT,
        relative_to: ty.Optional[Path] = None,
        ignore_hidden_files: bool = False,
        ignore_hidden_dirs: bool = False,
    ) -> ty.Dict[str, str]:
        """Calculate hashes for all files in the file-set based on the relative paths and
        contents of its constituent files

        Parameters
        ----------
        crypto : function, optional
            the cryptography method used to hash the files, by default hashlib.sha256
        **kwargs
            keyword args passed directly through to the ``hash_dir`` function

        Returns
        -------
        file_hashes : dict[str, bytes]
            unique hashes for each file in the file-set
        """
        if crypto is None:
            crypto = hashlib.sha256
        file_hashes = {}
        for path, bytes_iter in self.byte_chunks(
            mtime=mtime,
            chunk_len=chunk_len,
            relative_to=relative_to,
            ignore_hidden_files=ignore_hidden_files,
            ignore_hidden_dirs=ignore_hidden_dirs,
        ):
            crypto_obj = crypto()
            for bytes_str in bytes_iter:
                crypto_obj.update(bytes_str)
            file_hashes[str(path)] = crypto_obj.hexdigest()
        return file_hashes

    @classmethod
    def referenced_types(cls) -> ty.Set[ty.Type[Classifier]]:
        """Returns a flattened list of nested types referenced within the fileset type

        Returns
        -------
        types : set[Classifier]
            all the types that are referenced in shape or form within the class
        """
        types: ty.Set[ty.Type[Classifier]] = set([cls])
        for b in cls.__mro__:
            try:
                nested = b.nested_types  # type: ignore
            except AttributeError:
                continue
            for t in nested:
                types.update(t.referenced_types())
        return types

    @classmethod
    def mock(cls, *fspaths: ty.Union[Path, str]) -> "Self":
        """Return an instance of a mocked sub-class of the file format to be used in
        test routines like doctests that doesn't require to point at actual files

        Parameters
        ----------
        *fspaths: sequence[Path | str]
            the paths to be provided to the mocked class, by default will be ["mock/<class-name-lower>"]

        Returns
        -------
        Self
            a file-set that will pass type-checking as an instance of the given
            fileset class but which doesn't actually point to any FS objects.
        """
        mock_cls: ty.Type[Self] = type(
            cls.__name__ + "Mock", (MockMixin, cls), {"TRUE_CLASS": cls}
        )
        fspaths_lst = list(fspaths)
        if not fspaths:
            fspaths_lst = []
            fspath = f"/mock/{cls.__name__.lower()}"
            if cls.ext:
                fspath += cls.ext
            fspaths_lst.append(fspath)
        return mock_cls(fspaths=fspaths_lst)

    @classmethod
    def sample(
        cls,
        dest_dir: ty.Optional[Path] = None,
        seed: ty.Union[int, str] = 0,
        stem: ty.Optional[str] = None,
    ) -> Self:
        """Return an sample instance of the file-set type for classes where the
        `test_data` extra has been implemented

        Parameters
        ----------
        dest_dir : Path, optional
            the path in which to create the test data
        seed : int
            seed used to generate content. Defaults to 0 (rather than a timestamp), so
            the default method call produces consistent runs between calls
        stem : str
            the filename stem to give the file

        Returns
        -------
        FileSet
            an instance of the given file-set class
        """
        if not dest_dir:
            dest_dir = Path(tempfile.mkdtemp())
        # Need to use mock to get an instance in order to use the singledispatch-based
        # extra decorator
        fspaths = cls.sample_data(
            SampleFileGenerator(dest_dir=dest_dir, seed=seed, fname_stem=stem)
        )
        try:
            obj = cls(fspaths)
        except FormatMismatchError as e:
            raise NotImplementedError(
                f"File paths generated by override of Fileset.generate_sample_data() "
                f"({fspaths}) do not match '{cls.mime_like}' file type. A more specific "
                f"implementation is required. Reason:\n\n{e}"
            )
        return obj

    @classmethod
    def sample_data(cls, generator: SampleFileGenerator) -> ty.Iterable[Path]:
        """Converts the `generate_sample_data` method into a class method by mocking up
        a class instance and calling the method on it

        Parameters
        ----------
        generator : SampleFileGenerator
            the generator to use to create the sample data

        Returns
        -------
        ty.Iterable[Path]
            the generated file-system paths
        """
        mock: FileSet = cls.mock()
        return mock.generate_sample_data(generator)

    @extra
    def generate_sample_data(
        self,
        generator: SampleFileGenerator,
    ) -> ty.List[Path]:
        """Generate test data at the fspaths of the file-set

        Parameters
        ----------
        dest_dir : Path
            the directory to generate the test data within
        seed : int
            seed used to generate content. Defaults to 0 (rather than a timestamp), so
            the default method call produces consistent runs between calls
        stem : str
            the filename stem to give the file

        Returns
        -------
        fspaths : ty.Iterable
            the generated fspaths
        """
        raise NotImplementedError

    class ExtensionDecomposition(IntEnum):
        """What to consider the file extension to be for paths without an explicitly
        defined extension

        Options
        -------
        none
            assume it doesn't have a file extension, i.e. all parts are included in the stem
        single
            assume that anything after the last '.' is the extension, e.g. the extension
            of "file.nii.gz" would be ".gz"
        multiple
            assume that anything after the first '.' is the extension, e.g. the extension
            of "file.nii.gz" would be "nii.gz"
        """

        none = 0
        single = 1
        multiple = 2

        def __str__(self) -> str:
            return self.name

    def decomposed_fspaths(
        self,
        required_only: bool = True,
        decomposition_mode: ExtensionDecomposition = ExtensionDecomposition.single,
    ) -> ty.List[ty.Tuple[Path, str, str]]:
        """Decompose paths into parent directory, filename stem, and extension

        Parameters
        ----------
        required_only : bool, optional
            only include required paths, by default True
        assume_implicit_ext : FileSet.ExtensionDecomposition, optional
            how to interpret paths without an explicitly defined extension (i.e. by
            either the extension of the FileSet or nested filesets), by default single

        Returns
        -------
        decomposed_fspath : list[tuple[Path, str, str]]
            a tuple consisting of the parent directory, file-stem and extension
        """
        from ..generic import File

        decomposed_fspaths = []
        implicit = set(
            self.required_paths()
            if required_only and self.required_paths()
            else self.fspaths
        )
        nested = self.nested_filesets()
        for fileset in [self] + nested:
            if isinstance(fileset, File):
                try:
                    decomposed = (
                        fileset.fspath.parent,
                        fileset.stem,
                        fileset.actual_ext,
                    )
                except UnconstrainedExtensionException:
                    implicit.add(fileset.fspath)
                    continue
                if fileset.fspath in implicit:
                    decomposed_fspaths.append(decomposed)
                    implicit.remove(fileset.fspath)
                elif decomposed not in decomposed_fspaths:
                    previous_fileset = next(
                        f
                        for f in nested
                        if isinstance(f, File) and f.fspath == fileset.fspath
                    )
                    previous = (
                        previous_fileset.fspath.parent,
                        previous_fileset.stem,
                        previous_fileset.actual_ext,
                    )
                    warn(
                        f"The '{fileset.fspath}' path within {self} into has been decomposed as "
                        f"{previous} as it was interpreted as a {type(previous_fileset)} "
                        f"file, whereas it is also interpreted as a {type(fileset)}, in "
                        f"which case it could be alternatively decomposed into {decomposed}"
                    )
        for fspath in implicit:
            decomposed_fspaths.append(
                self.decompose_fspath(fspath, mode=decomposition_mode)
            )
        return decomposed_fspaths

    @classmethod
    def decompose_fspath(
        cls,
        fspath: ty.Union[Path, str],
        mode: ExtensionDecomposition = ExtensionDecomposition.single,
    ) -> ty.Tuple[Path, str, str]:
        if isinstance(fspath, str):
            fspath = Path(fspath)
        """Decompose an arbitrary file-system path into parent dir, stem and extension
        given the assumption on what constitutes an extension"""
        if mode == cls.ExtensionDecomposition.multiple:
            ext = "".join(fspath.suffixes)
            stem = fspath.name[: -len(ext)]
        elif mode == cls.ExtensionDecomposition.single:
            stem = fspath.stem
            ext = fspath.suffix
        else:
            assert mode == cls.ExtensionDecomposition.none
            stem = str(fspath)
            ext = ""
        return fspath.parent, stem, ext

    @classmethod
    def from_paths(
        cls, fspaths: ty.Iterable[Path], common_ok: bool = False
    ) -> ty.Tuple[ty.Set[Self], ty.Set[Path]]:
        """Finds all instances of the fileset class that can be constructed from a
        collection of file-system paths.

        Parameters
        ----------
        fspaths : Iterable[Path]
            file-system paths to instantiate file-sets from
        common_ok : bool
            whether secondary file-system paths can be shared between multiple instances
            of the returned filesets

        Returns
        -------
        filesets : set[FileSet]
            file-sets instantiated from the provided paths
        remaining : set[Path]
            remaining file-system paths that weren't used in any of the file-sets
        """
        fspaths = [Path(p) for p in fspaths]
        filesets = set()
        remaining = set(fspaths)
        for fspath in fspaths:
            try:
                fileset = cls(fspath)
            except FormatMismatchError:
                continue
            else:
                filesets.add(fileset)
                fileset.trim_paths()  # only included required paths in the file set
                if not common_ok and not all(p in remaining for p in fileset.fspaths):
                    continue
                for p in fileset.fspaths:
                    remaining.remove(p)
        return filesets, remaining

    class CopyMode(Enum):
        """Designates the desired behaviour of the FileSet.copy() method with regards to
        symbolic, hard or full copies

        Basic options (in order of preference)
        --------------------------------------
        leave
            simply leave the files where they are (i.e. do nothing)
        symlink
            symlink the files into the destination directory
        hardlink
            hardlink the files into the destination directory
        copy
            duplicate (actually copy) the files into the destination directory

        Common combinations
        -------------------
        link
            use either linking method (preferring symbolic)
        link_or_copy
            use either link method or copy (preferring sym, hard, then copy)
        symlink_or_copy
            "  symbolic "   "         "
        hardlink_or_copy
            "  hard "   "         "

        Masks
        -----
        any
            use any method (preferring leave)
        none
            none of the requested methods are supported (i.e. after masking with the
            supported options mask)
        """

        # Bases

        leave = 0b0001
        symlink = 0b0010
        hardlink = 0b0100
        copy = 0b1000

        # Common combinations

        link = 0b0110
        link_or_copy = 0b1110
        symlink_or_copy = 0b1010
        hardlink_or_copy = 0b1100

        # Masks

        any = 0b1111
        none = 0b0000

        # All other combinations (typically the result of bit-masking)

        leave_or_copy = 0b1001
        leave_or_symlink = 0b0011
        leave_or_hardlink = 0b0101
        leave_or_link = 0b0111
        leave_or_symlink_or_copy = 0b1011
        leave_or_hardlink_or_copy = 0b1101

        def __xor__(self, other: "FileSet.CopyMode") -> "FileSet.CopyMode":
            return type(self)(self.value ^ other.value)

        def __and__(self, other: "FileSet.CopyMode") -> "FileSet.CopyMode":
            return type(self)(self.value & other.value)

        def __or__(self, other: "FileSet.CopyMode") -> "FileSet.CopyMode":
            return type(self)(self.value | other.value)

        def __sub__(self, other: "FileSet.CopyMode") -> "FileSet.CopyMode":
            return type(self)((self.value & (self.value ^ other.value)))

        def __bool__(self) -> bool:
            return bool(self.value)

        def __str__(self) -> str:
            return self.name

    class CopyCollation(IntEnum):
        """Designates the desired "collation" behaviour of the FileSet.copy() method

        Values
        ------
        any
            If mode == leave, paths can exist in separate directories in the
            file-system. For other copy modes, the relative directory structure between
            the "copied" paths (incl. links) in the set will be maintained within the
            destination directory. This guarantees there won't be name clashes between
            copied paths.
        siblings
            copied paths are guaranteed to be "copied" to the root of the destination
            directory, i.e be siblings. However, this requires that the file/dir name
            in fspaths are unique.
        adjacent
            copied paths are guaranteed to have the same name-stem and only differ in
            file-extensions. Requires that the file-set only includes files/dirs with
            unique suffixes (NB: suffixes are considered to be everything after the first
            '.' in the filename).
        """

        any = 0
        siblings = 1
        adjacent = 2

        def __str__(self) -> str:
            return self.name

    def copy(
        self,
        dest_dir: PathType,
        mode: ty.Union[CopyMode, str] = CopyMode.copy,
        collation: ty.Union[CopyCollation, str] = CopyCollation.any,
        new_stem: ty.Optional[str] = None,
        trim: bool = True,
        make_dirs: bool = False,
        overwrite: bool = False,
        supported_modes: CopyMode = CopyMode.any,
        extension_decomposition: ExtensionDecomposition = ExtensionDecomposition.single,
    ) -> Self:
        """Copies the file-set to a new directory, optionally renaming the files
        to have consistent name-stems.

        Based on the range of options provided, copy determines the "laziest" mode to use,
        i.e. if we can leave the files where they are and satisfy both the explicit mode
        requested by the user and the "collation" requirements (see FileSet.CopyCollation),
        we prefer to do so, otherwise we prefer to symlink, then hardlink,
        then as a last resort a full copy.

        Parameters
        ----------
        dest_dir : str
            Path to the parent directory to save the file-set
        mode : FileSet.CopyMode or str, optional
            designates whether to perform an actual copy or whether a link (symbolic or
            hard) is okay, 'duplicate' by default. See FielSet.CopyMode for details
        collation : FileSet.CopyCollation or str, optional
            how to treat relative paths within the fileset, i.e. whether to move them
            to a single directory, rename them to the same file-stem or maintain
            relative directory structure. See FileSet.CopyCollation for details
        new_stem: str, optional
            the file name excluding file extensions, to give the files/dirs in the parent
            directory, by default the original file name is used
        trim : bool, optional
            Only copy the paths in the file-set that are "required" by the format,
            true by default
        make_dirs : bool, optional
            Make the parent destination and all missing ancestors if they are missing,
            false by default
        overwrite : bool, optional
            whether to overwrite existing files/directories if present
        supported_modes : CopyMode, optional
            supported modes for the copy operation. Used to mask out the requested
            copy mode
        extension_decomposition : FileSet.ExtensionDecomposition, optional
            whether to consider file extensions to start from the first '.' (multiple) or the
            last (single) or be empty (none), when the extension of a fspath in the
            FileSet isn't explicitly defined by the FileSet class. Only relevant when
            collation mode is set to "adjacent". By default True
        """
        dest_dir = Path(dest_dir)
        # Logic to determine the laziest mode to use
        mode = self.CopyMode[mode] if isinstance(mode, str) else mode
        if len(self.fspaths) == 1:
            # If there is only one path to copy, then collation isn't meaningful
            collation = self.CopyCollation.any
        else:
            collation = (
                self.CopyCollation[collation]
                if isinstance(collation, str)
                else collation
            )
        # Rule out any copy modes that are not supported given the collation mode
        # and file-system mounts the paths and destination directory reside on
        constraints = []
        if (
            not FsMountIdentifier.symlinks_supported(dest_dir)
            and mode & self.CopyMode.symlink
        ):
            supported_modes -= self.CopyMode.symlink
            constraint = (
                f"Destination directory is on CIFS mount ({dest_dir}) "
                "and we therefore cannot create a symlink"
            )
            logger.debug(constraint)
            constraints.append(constraint)
        not_on_same_mount = [
            p for p in self.fspaths if not FsMountIdentifier.on_same_mount(p, dest_dir)
        ]
        if not_on_same_mount and mode & self.CopyMode.hardlink:
            supported_modes -= self.CopyMode.hardlink
            constraint = (
                f"Some paths ({', '.join(str(p) for p in not_on_same_mount)}) are on "
                f"not on same file-system mount as the destination directory {dest_dir}"
                "and therefore cannot be hard-linked"
            )
            logger.debug(constraint)
            constraints.append(constraint)
        if new_stem or (
            collation >= self.CopyCollation.siblings
            and not all(p.parent == self.parent for p in self.fspaths)
        ):
            supported_modes -= self.CopyMode.leave

        # Get the intersection of copy modes that are supported and have been requested
        selected_mode = mode & supported_modes
        if not selected_mode:
            msg = (
                f"Cannot copy {self} using '{mode}' mode as it is not supported by "
                f"the '{supported_modes}' given the collation specification, {collation}"
            )
            if constraints:
                msg += ", and the following constraints:\n" + "\n".join(constraints)
            raise UnsatisfiableCopyModeError(msg)
        if selected_mode & self.CopyMode.leave:
            return self  # Don't need to do anything

        copy_file: ty.Callable[[Path, Path], None]
        copy_dir: ty.Callable[[Path, Path], None]

        # Select inner copy/link methods
        if selected_mode & self.CopyMode.symlink:
            copy_dir = copy_file = os.symlink
        elif selected_mode & self.CopyMode.hardlink:
            copy_file = os.link

            def hardlink_dir(src: Path, dest: Path) -> None:
                for dpath_str, _, fpaths in os.walk(src):
                    dpath = Path(dpath_str)
                    relpath = dpath.relative_to(src)
                    (dest / relpath).mkdir()
                    for fpath in fpaths:
                        os.link(dpath / fpath, dest / relpath / fpath)

            copy_dir = hardlink_dir
        else:
            assert selected_mode & self.CopyMode.copy
            copy_dir = shutil.copytree
            copy_file = shutil.copyfile  # type: ignore

        # Get the paths that need to be copied, checking that it is possible to achieve
        # the requested collation mode
        fspaths_to_copy, new_stem = self._fspaths_to_copy(
            new_stem=new_stem,
            trim=trim,
            collation=collation,
            extension_decomposition=extension_decomposition,
        )

        # Prepare destination directory
        dest_dir = Path(dest_dir)
        if make_dirs:
            dest_dir.mkdir(parents=True, exist_ok=True)

        # Iterate through the paths to copy, copying them to the destination directory
        new_paths = []
        for fspath in fspaths_to_copy:
            new_path, fspath = self._new_copy_path(
                dest_dir=dest_dir, fspath=fspath, new_stem=new_stem, collation=collation
            )
            if new_path.exists():
                if overwrite:
                    if fspath.is_dir():
                        shutil.rmtree(new_path)
                    else:
                        os.unlink(new_path)
                else:
                    raise FileExistsError(
                        f"Destination path '{str(new_path)}' exists, set "
                        "'overwrite' to overwrite it"
                    )
            if fspath.is_dir():
                copy_dir(fspath, new_path)
            else:
                copy_file(fspath, new_path)
            new_paths.append(new_path)
        return type(self)(new_paths)

    def move(
        self,
        dest_dir: PathType,
        collation: ty.Union[CopyCollation, str] = CopyCollation.any,
        new_stem: ty.Optional[str] = None,
        trim: bool = True,
        make_dirs: bool = False,
        overwrite: bool = False,
        extension_decomposition: ExtensionDecomposition = ExtensionDecomposition.single,
    ) -> Self:
        """Moves the file-set to a new directory, optionally renaming the files
        to have consistent name-stems.

        Parameters
        ----------
        dest_dir : str
            Path to the parent directory to save the file-set
        collation : FileSet.CopyCollation or str, optional
            how to treat relative paths within the fileset, i.e. whether to move them
            to a single directory, rename them to the same file-stem or maintain
            relative directory structure. See FileSet.CopyCollation for details
        new_stem: str, optional
            the file name excluding file extensions, to give the files/dirs in the parent
            directory, by default the original file name is used
        trim : bool, optional
            Only copy the paths in the file-set that are "required" by the format,
            true by default
        make_dirs : bool, optional
            Make the parent destination and all missing ancestors if they are missing,
            false by default
        overwrite : bool, optional
            whether to overwrite existing files/directories if present
        extension_decomposition : FileSet.ExtensionDecomposition, optional
            whether to consider file extensions to start from the first '.' (multiple) or the
            last (single) or be empty (none), when the extension of a fspath in the
            FileSet isn't explicitly defined by the FileSet class. Only relevant when
            collation mode is set to "adjacent". By default True
        """
        dest_dir = Path(dest_dir)
        if len(self.fspaths) == 1:
            # If there is only one path to copy, then collation isn't meaningful
            collation = self.CopyCollation.any
        else:
            collation = (
                self.CopyCollation[collation]
                if isinstance(collation, str)
                else collation
            )
        fspaths_to_move, new_stem = self._fspaths_to_copy(
            new_stem=new_stem,
            trim=trim,
            collation=collation,
            extension_decomposition=extension_decomposition,
        )
        dest_dir = Path(dest_dir)  # ensure a Path not a string
        if make_dirs:
            dest_dir.mkdir(parents=True, exist_ok=True)

        new_paths = []
        for fspath in fspaths_to_move:
            new_path, fspath = self._new_copy_path(
                dest_dir=dest_dir, fspath=fspath, new_stem=new_stem, collation=collation
            )
            if new_path.exists():
                if overwrite:
                    if fspath.is_dir():
                        shutil.rmtree(new_path)
                    else:
                        os.unlink(new_path)
                else:
                    raise FileExistsError(
                        f"Destination path '{str(new_path)}' exists, set "
                        "'overwrite' to overwrite it"
                    )
            shutil.move(str(fspath), new_path)
            new_paths.append(new_path)
        return type(self)(new_paths)

    def _fspaths_to_copy(
        self,
        new_stem: ty.Optional[str],
        trim: bool,
        collation: CopyCollation,
        extension_decomposition: ExtensionDecomposition,
    ) -> ty.Tuple[
        ty.Iterable[ty.Union[Path, ty.Tuple[Path, str, str]]], ty.Optional[str]
    ]:
        """Returns the file-paths to be copied/moved based on the collation mode and
        new_stem

        Parameters
        ----------
        new_stem : str
            the file name excluding file extensions, to give the files/dirs in the parent
            directory, by default the original file name is used
        trim : bool
            Only copy the paths in the file-set that are "required" by the format, true
            by default"
        collation : FileSet.CopyCollation or str
            how to treat relative paths within the fileset, i.e. whether to move them to a
            single directory, rename them to the same file-stem or maintain relative
            directory structure. See FileSet.CopyCollation for details
        extension_decomposition : FileSet.ExtensionDecomposition
            whether to consider file extensions to start from the first '.' (multiple) or
            the last (single) or be empty (none), when the extension of a fspath in the
            FileSet isn't explicitly defined by the FileSet class. Only relevant when
            collation mode is set to "adjacent". By default True

        Returns
        -------
        fspaths_to_copy : Iterable[Path | tuple[Path, str, str]]:
            the file-paths to be copied/moved
        new_stem : str
            the new stem to use for the file-paths, which is the determined if not
            provided explicitly and collation mode is "adjacent"
        """
        if collation >= self.CopyCollation.siblings:
            duplicate_names = [
                n for n, c in Counter(p.name for p in self.fspaths).items() if c > 1
            ]
            if duplicate_names:
                raise UnsatisfiableCopyModeError(
                    f"Cannot copy/move {self} with collation mode "
                    f'"{collation}", as there are duplicate filenames, {duplicate_names}, '
                    f"in file paths: " + "\n".join(str(p) for p in self.fspaths)
                )
        if collation == self.CopyCollation.adjacent or new_stem:
            decomposed_fspaths = self.decomposed_fspaths(
                required_only=trim, decomposition_mode=extension_decomposition
            )
            exts = [d[-1] for d in decomposed_fspaths]
            duplicate_exts = [n for n, c in Counter(exts).items() if c > 1]
            if duplicate_exts:
                raise UnsatisfiableCopyModeError(
                    f"Cannot copy/move {self} with collation mode "
                    f'"{collation}", as there are duplicate extensions, {duplicate_exts}, '
                    f"in file paths: " + "\n".join(str(p) for p in self.fspaths)
                )
        fspaths_to_copy: ty.Iterable[ty.Union[Path, ty.Tuple[Path, str, str]]]
        if trim and self.required_paths():
            fspaths_to_copy = self.required_paths()
        else:
            fspaths_to_copy = self.fspaths
        if not fspaths_to_copy:
            raise UnsatisfiableCopyModeError(
                f"Cannot copy {self} because none of the fspaths in the file-set are "
                "required. Set trim=False to copy all file-paths"
            )
        # Set default for new_stem if not provided and collating file-set to be adjacent
        if collation == self.CopyCollation.adjacent:
            if new_stem is None:
                new_stem = sorted(decomposed_fspaths)[0][1]
        # WARNING we redefine fspaths_to_copy as list of tuples not Paths

        if new_stem:
            fspaths_to_copy = decomposed_fspaths
        return fspaths_to_copy, new_stem

    def _new_copy_path(
        self,
        dest_dir: Path,
        fspath: ty.Union[Path, ty.Tuple[Path, str, str]],
        new_stem: ty.Optional[str],
        collation: CopyCollation,
    ) -> ty.Tuple[Path, Path]:
        """Returns the new path for a file to be copied/moved based on the collation mode
        and new_stem

        Parameters
        ----------
        dest_dir : Path
            the destination directory
        fspath : Path | tuple[Path, str, str]
            the file-path to be copied/moved
        new_stem : str
            the file name excluding file extensions, to give the files/dirs in the parent
            directory, by default the original file name is used
        collation : FileSet.CopyCollation
            how to treat relative paths within the fileset, i.e. whether to move them to a
            single directory, rename them to the same file-stem or maintain relative
            directory structure. See FileSet.CopyCollation for details

        Returns
        -------
        new_path : Path
            the new path for the file to be copied/moved
        fspath : Path
            the original file-path, reconstructed from its decomposition if necessary
        """
        if not isinstance(fspath, Path):
            assert new_stem
            # fspath is a path decomposed into parent, stem, ext instead of a Path
            parent_dir, old_stem, ext = fspath
            fspath = parent_dir / (old_stem + ext)  # reconstruct into a Path
            new_path = dest_dir / (new_stem + ext)
        elif collation == self.CopyCollation.siblings:
            new_path = dest_dir / fspath.name
        else:
            assert collation == self.CopyCollation.any
            new_path = dest_dir / fspath.relative_to(self.parent)
            new_path.parent.mkdir(parents=True, exist_ok=True)
        return new_path, fspath

    # Class attributes, used to cache the results of the class methods
    _all_formats: ty.Optional[ty.Set[ty.Type["FileSet"]]] = None
    _formats_by_iana_mime: ty.Optional[ty.Dict[str, ty.Type["FileSet"]]] = None
    _formats_by_name: ty.Optional[ty.Dict[str, ty.Set[ty.Type["FileSet"]]]] = None
    _required_props: ty.Optional[ty.Tuple[str, ...]] = None
    _valid_class: ty.Optional[bool] = None


class MockMixin:
    """Strips out validation methods of a class, allowing it to be mocked in a way that
    still satisfies type-checking"""

    def __init__(
        self,
        fspaths: FspathsInputType,
        metadata: ty.Union[ty.Dict[str, ty.Any], bool, None] = False,
    ):
        self.fspaths = fspaths_converter(fspaths)
        self._metadata = metadata

    @classproperty
    def type_name(cls) -> str:
        class_name: str = cls.__name__  # type: ignore
        assert class_name.endswith("Mock")
        return class_name[: -len("Mock")]

    def byte_chunks(
        self,
        mtime: bool = False,
        chunk_len=FILE_CHUNK_LEN_DEFAULT,
        relative_to: ty.Optional[os.PathLike] = None,
        ignore_hidden_files: bool = False,
        ignore_hidden_dirs: bool = False,
    ):
        if relative_to is None:
            relative_to = os.path.commonpath(self.fspaths)
        else:
            relative_to = str(relative_to)
        for key, fspath in sorted(
            ((str(p)[len(relative_to) :], p) for p in self.fspaths),
            key=itemgetter(0),
        ):
            yield (key, iter([key.encode()]))  # empty iterator as files don't exist

    @classproperty
    def namespace(cls) -> str:
        """The "namespace" the format belongs to under the "fileformats" umbrella
        namespace"""
        mro: ty.Tuple[ty.Type] = cls.__mro__  # type: ignore
        for base in mro:
            if issubclass(base, MockMixin):
                continue
            try:
                return base.namespace  # type: ignore
            except FormatDefinitionError:
                pass
        raise FormatDefinitionError(
            f"None of of the bases classes of {cls} ({mro}) have a valid namespace"
        )
