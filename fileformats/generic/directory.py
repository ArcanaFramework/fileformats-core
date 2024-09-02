import typing as ty
import hashlib
from pathlib import Path
from fileformats.core.exceptions import FormatMismatchError
from fileformats.core.utils import classproperty
from .fsobject import FsObject
from fileformats.core.fileset import FileSet, FILE_CHUNK_LEN_DEFAULT
from fileformats.core.mixin import WithClassifiers


class Directory(FsObject):
    """Base directory to be overridden by subtypes that represent directories but don't
    want to inherit content type "qualifers" (i.e. most of them)"""

    content_types: ty.Tuple[ty.Type[FileSet], ...] = ()

    @property
    def fspath(self) -> Path:
        # fspaths are checked for existence with the exception of mock classes
        dirs = [p for p in self.fspaths if not p.is_file()]
        if not dirs:
            raise FormatMismatchError(f"No directory paths provided {repr(self)}")
        if len(dirs) > 1:
            raise FormatMismatchError(
                f"More than one directory path provided {dirs} to {repr(self)}"
            )
        fspath = dirs[0]
        missing = []
        for content_type in self.content_types:
            match = False
            for p in fspath.iterdir():
                try:
                    content_type([p])
                except FormatMismatchError:
                    continue
                else:
                    match = True
                    break
            if not match:
                missing.append(content_type)
        if missing:
            raise FormatMismatchError(
                f"Did not find matches for {missing} content types in {repr(self)}"
            )
        return fspath

    @property
    def contents(self) -> ty.Iterable[FileSet]:
        for content_type in self.content_types:
            assert content_type
            for p in self.fspath.iterdir():
                try:
                    yield content_type([p])
                except FormatMismatchError:
                    continue

    @classproperty
    def unconstrained(cls) -> bool:
        """Whether the file-format is unconstrained by extension, magic number or another
        constraint"""
        return super().unconstrained and not cls.content_types

    def is_dir(self) -> bool:
        return True

    def is_file(self) -> bool:
        return False

    @property
    def _validate_contents(self) -> None:
        if not self.content_types:
            return
        not_found = set(self.content_types)
        for fspath in self.fspath.iterdir():
            for content_type in list(not_found):
                if content_type.matches(fspath):
                    not_found.remove(content_type)
                    if not not_found:
                        return
        assert not_found
        raise FormatMismatchError(
            f"Did not find the required content types, {not_found}, within the "
            f"directory {self.fspath} of {self}"
        )

    def hash_files(
        self,
        crypto: ty.Optional[ty.Callable[[], "hashlib._hashlib.HASH"]] = None,
        mtime: bool = False,
        chunk_len: int = FILE_CHUNK_LEN_DEFAULT,
        relative_to: ty.Optional[Path] = None,
        ignore_hidden_files: bool = False,
        ignore_hidden_dirs: bool = False,
    ) -> ty.Dict[str, str]:
        if relative_to is None:
            relative_to = self.fspath
        return super().hash_files(
            crypto=crypto,
            mtime=mtime,
            chunk_len=chunk_len,
            relative_to=relative_to,
            ignore_hidden_files=ignore_hidden_files,
            ignore_hidden_dirs=ignore_hidden_dirs,
        )

    # Duck-type Path methods

    def __div__(self, other: ty.Union[str, Path]) -> Path:
        return self.fspath / other

    def glob(self, pattern: str) -> ty.Iterator[Path]:
        return self.fspath.glob(pattern)

    def rglob(self, pattern: str) -> ty.Iterator[Path]:
        return self.fspath.rglob(pattern)

    def joinpath(self, other: ty.Union[str, Path]) -> Path:
        return self.fspath.joinpath(other)

    def iterdir(self) -> ty.Iterator[Path]:
        return self.fspath.iterdir()


class DirectoryOf(WithClassifiers, Directory):
    """Generic directory classified by the formats of its contents"""

    # WithClassifiers-required class attrs
    classifiers_attr_name = "content_types"
    allowed_classifiers = (FileSet,)
    generically_classifiable = True
