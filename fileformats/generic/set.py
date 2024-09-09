import typing as ty
from fileformats.core.fileset import FileSet
from fileformats.core.exceptions import (
    FormatMismatchError,
)
from fileformats.core.decorators import contents_property
from fileformats.core.mixin import WithClassifiers


class TypedSet(FileSet):
    """List of specific file types (similar to the contents of a directory but not
    enclosed in one)"""

    content_types: ty.Tuple[ty.Type[FileSet], ...] = ()

    @contents_property
    def contents(self) -> ty.Iterable[FileSet]:
        for content_type in self.content_types:
            for p in self.fspaths:
                try:
                    yield content_type([p])
                except FormatMismatchError:
                    continue

    @property
    def _validate_contents(self) -> None:
        if not self.content_types:
            return
        not_found = set(self.content_types)
        for fspath in self.fspaths:
            for content_type in list(not_found):
                if content_type.matches(fspath):
                    not_found.remove(content_type)
                    if not not_found:
                        return
        assert not_found
        raise FormatMismatchError(
            f"Did not find the required content types, {not_found}, within the "
            f"given list {self.fspaths}"
        )


class SetOf(WithClassifiers, TypedSet):
    # WithClassifiers-required class attrs
    classifiers_attr_name = "content_types"
    allowed_classifiers = (FileSet,)
    generically_classifiable = True
