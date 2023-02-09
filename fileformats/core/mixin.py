from pathlib import Path
import inspect
import typing as ty
from collections import Counter
from . import mark
from .base import FileSet
from .utils import classproperty
from .converter import _GenericConversionTarget
from .exceptions import FileFormatsError, FormatMismatchError


class WithMagicNumber:
    """Mixin class for Files with magic numbers at the start of their
    contents.

    Class Attrs
    -----------
    magic_number : str
        the magic number/string to search for at the start of the file
    binary : bool
        if the file-format is a binary type then this flag needs to be set in order to
        read the contents properly
    magic_number_offset : int, optional
        the offset in bytes from the start of the file that the magic number is stored
    """

    magic_number_offset = 0

    @mark.check
    def check_magic_number(self):
        if self.binary and isinstance(self.magic_number, str):
            magic_bytes = bytes.fromhex(self.magic_number)
        else:
            magic_bytes = self.magic_number
        read_magic_number = self.read_contents(
            len(magic_bytes), offset=self.magic_number_offset
        )
        if read_magic_number != magic_bytes:
            if self.binary and isinstance(self.magic_number, str):
                read_magic_str = '"' + bytes.hex(read_magic_number) + '"'
                magic_str = '"' + self.magic_number + '"'
            else:
                read_magic_str = read_magic_number
                magic_str = self.magic_number
            raise FormatMismatchError(
                f"Magic number of file {read_magic_str} doesn't match expected "
                f"{magic_str}"
            )


class WithAdjacentFiles:
    """
    If only the main fspath is provided to the __init__ of the class, this mixin
    automatically includes any "adjacent files", i.e. any files with the same stem but
    different extensions

    Note that WithAdjacentFiles must come before the primary type in the method-resolution
    order of the class so it can override the '__attrs_post_init__' method in
    post_init_super class (typically FileSet), e.g.

        class MyFileFormatWithSeparateHeader(WithSeparateHeader, MyFileFormat):

            header_type = MyHeaderType

    Class Attrs
    -----------
    post_init_super : type
        the format class the WithAdjacentFiles mixin is mixed with that defines the
        __attrs_post_init__ method that should be called once the adjacent files
        are added to the self.fspaths attribute to run checks.
    """

    post_init_super = FileSet

    def __attrs_post_init__(self):
        if len(self.fspaths) == 1:
            self.fspaths |= self.get_adjacent_files()
            trim = True
        else:
            trim = False
        self.post_init_super.__attrs_post_init__(self)
        if trim:
            self.trim_paths()

    def get_adjacent_files(self) -> ty.Set[Path]:
        stem = self.stem
        adjacents = set()
        for sibling in self.fspath.parent.iterdir():
            if (
                sibling is not self.fspath
                and sibling.is_file()
                and sibling.name.startswith(stem)
            ):
                adjacents.add(sibling)
        return adjacents


class WithSeparateHeader(WithAdjacentFiles):
    """Mixin class for Files with metadata stored in separate header files (typically
    with the same file stem but differing extension)

    Note that WithSeparateHeader must come before the primary type in the method-resolution
    order of the class so it can override the '__attrs_post_init__' method, e.g.

        class MyFileFormatWithSeparateHeader(WithSeparateHeader, MyFileFormat):

            header_type = MyHeaderType

    Class Attrs
    -----------
    header_type : type
        the file-format of the header file
    """

    @mark.required
    @property
    def header(self):
        return self.header_type(self.select_by_ext(self.header_type))

    def load_metadata(self):
        return self.header.load()


class WithSideCar(WithAdjacentFiles):
    """Mixin class for Files with a "side-car" file that augments the inline metadata
    (typically with the same file stem but differing extension).

    Note that WithSideCar must come before the primary type in the method-resolution
    order of the class so it can override the '__attrs_post_init__' and 'load_metadata'
    methods, e.g.

        class MyFileFormatWithSideCar(WithSideCar, MyFileFormat):

            primary_type = MyFileFormat
            side_car_type = MySideCarType

    Class Attrs
    -----------
    primary_type : type
        the file-format of the primary file (used to read the inline metadata), can be
        the base class that implements 'load_metadata'
    side_car_type : type
        the file-format of the header file
    """

    @mark.required
    @property
    def side_car(self):
        return self.side_car_type(self.select_by_ext(self.side_car_type))

    def load_metadata(self):
        metadata = self.primary_type.load_metadata(self)
        side_car_data = self.side_car.load()
        overlap = set(metadata) & set(side_car_data)
        if overlap:
            raise FileFormatsError(
                "Detected overlap between header values and side-car values:\n"
                "\n".join(
                    f"{k}: header={metadata[k]}, side-car={side_car_data[k]}"
                    for k in overlap
                )
            )
        metadata.update(side_car_data)
        return metadata


class WithQualifiers:
    """Mixin class for adding the ability to qualify the format class to designate the
    type of information stored within the format, e.g. ``Directory[Png, Gif]`` for a
    directory containing PNG and GIF files, ``Zip[DataFile]`` for a zipped data file,
    ``Array[Integer]`` for an array containing integers, or DicomDir[T1w, Brain] for a
    T1-weighted MRI scan of the brain in DICOM format.

        class MyFormatWithContents(WithContents, File):

            ext = ".myf


        def my_func(file: MyFormatWithContents[Integer]):
            ...

    A unique class will be returned (i.e. multiple calls with the same arguments will
    return the same class)

    Class Attrs
    -----------
    qualifiers_attr_name : str, optional
        an attribute name to store the qualifiers at within the qualified class. This
        should be used if you need to reference the ``qualifiers`` attribute directly
        in any validation/other methods (i.e. in most cases), to handle the case of
        diamond inheritance between two classes that can be qualified.
        A default value should also be set in the unqualified base for this attribute,
        which is either ``()`` if ``multiple_qualifiers`` is true and ``None`` otherwise
    <qualifiers-attr-name> : tuple[type, ...] or None, optional
        pass a default value to the attribute referenced by 'qualifiers_attr_name'
    multiple_qualifiers : bool, optional
        whether or not multiple content types are permitted for the container type, True
    allowed_qualifiers : tuple[type,...], optional
        the allowable types (+ subclasses) for the content types. If None all types
        are allowed
    ordered_qualifiers : bool, optional
        whether the order of the content types is important or not, by default false
    genericly_qualified : bool, optional
        whether the class can be qualified by qualifiers in any namespace (true) or just the
        namespace it belongs to (false). If true, then the namespace of the genericly
        qualified class is omitted from the "mime-like" string. Note that the
        class' name therefore needs to be globally unique amongst all other genericly
        qualified classes and so it should be used sparingly, i.e., highly generic
        formats that are unambiguous across all namespaces, such as "directory", "zip",
        "gzip", "json", "yaml", etc...
    """

    qualifiers = ()  # qualifiers set in the current class
    _qualified_subtypes = {}
    # dict of previously created qualified subtypes. If an existing class with matching
    # qualifiers has been created it is returned instead of creating a new type. This
    # ensures that ``assert MyFormat[Qualifier] is MyFormat[Qualifier]``

    # Default values for class attrs
    multiple_qualifiers = True
    allowed_qualifiers = None
    ordered_qualifiers = False
    generically_qualified = False

    def __attrs_pre_init__(self):
        if self.wildcard_qualifiers():
            raise FileFormatsError(
                f"Can instantiate {type(self)} class as it has wildcard qualifiers "
                "and therefore should only be used for converter specifications"
            )

    @classproperty
    def is_qualified(cls):
        return "unqualified" in cls.__dict__

    @classmethod
    def wildcard_qualifiers(cls, qualifiers=None):
        if qualifiers is None:
            qualifiers = cls.qualifiers if cls.is_qualified else ()
        return frozenset(t for t in qualifiers if isinstance(t, ty.TypeVar))

    @classmethod
    def non_wildcard_qualifiers(cls, qualifiers=None):
        if qualifiers is None:
            qualifiers = cls.qualifiers if cls.is_qualified else ()
        return frozenset(q for q in qualifiers if not isinstance(q, ty.TypeVar))

    @classmethod
    def __class_getitem__(cls, qualifiers):
        """Set the content types for a newly created dynamically type"""
        if isinstance(qualifiers, ty.Iterable):
            qualifiers = tuple(qualifiers)
        else:
            qualifiers = (qualifiers,)
        if cls.allowed_qualifiers:
            not_allowed = [
                q
                for q in qualifiers
                if not (
                    isinstance(q, ty.TypeVar)
                    or any(q.is_subtype_of(t) for t in cls.allowed_qualifiers)
                )
            ]
            if not_allowed:
                raise FileFormatsError(
                    f"Invalid content types provided to {cls} (must be subclasses of "
                    f"{cls.allowed_qualifiers}): {not_allowed}"
                )
        # Sort content types if order isn't important
        if cls.multiple_qualifiers and not cls.ordered_qualifiers:
            repetitions = [t for t, c in Counter(qualifiers).items() if c > 1]
            if repetitions:
                raise FileFormatsError(
                    f"Cannot have more than one occurrence of a qualifier ({repetitions}) "
                    f"for {cls} class when {cls.__name__}.ordered_qualifiers is false"
                )
            qualifiers = frozenset(qualifiers)

        # Make sure that the "qualified" dictionary is present in this class not super
        # classes
        if "_qualified_subtypes" not in cls.__dict__:
            cls._qualified_subtypes = {}
        try:
            # Load previously created type so we can do ``assert MyType[Integer] is MyType[Integer]``
            qualified = cls._qualified_subtypes[qualifiers]
        except KeyError:
            class_attrs = {
                "unqualified": cls,
                "qualifiers": qualifiers,
            }
            if "qualifiers_attr_name" in cls.__dict__:
                if not hasattr(cls, cls.qualifiers_attr_name):
                    raise FileFormatsError(
                        f"Default value for qualifiers attribute "
                        f"'{cls.qualifiers_attr_name}' needs to be set in {cls}"
                    )
                class_attrs[cls.qualifiers_attr_name] = (
                    qualifiers if cls.multiple_qualifiers else qualifiers[0]
                )
            qualifier_names = [t.__name__ for t in qualifiers]
            if not cls.ordered_qualifiers:
                qualifier_names.sort()
            qualified = type(
                f"{'_'.join(qualifier_names)}__{cls.__name__}",
                (cls,),
                class_attrs,
            )
            cls._qualified_subtypes[qualifiers] = qualified
        return qualified

    @classmethod
    def get_converter_tuples(
        cls, source_format: type
    ) -> ty.List[ty.Tuple[ty.Callable, ty.Dict[str, ty.Any]]]:
        """Search the registered converters to find an appropriate task and associated
        key-word args to perform the conversion between source and target formats

        Parameters
        ----------
        source_format : type(FileSet)
            the source format to convert from
        """
        # Try to see if a converter has been defined to the exact type
        available_converters = super().get_converter_tuples(source_format)
        # Failing that, see if there is a generic conversion between the container type
        # the source format (or subclass of) defined with matching wildcards in the source
        # and target formats
        if not available_converters and cls.is_qualified and source_format.is_qualified:
            converters_dict = FileSet.get_converters_dict(cls.unqualified)
            for template_source_format, converter in converters_dict.items():
                if len(converter) == 3:  # was defined with wildcard qualifiers
                    converter, conv_kwargs, template_qualifiers = converter
                    wildcard_match = True
                    if template_source_format is _GenericConversionTarget:
                        assert tuple(cls.wildcard_qualifiers(template_qualifiers)) == (
                            template_source_format,
                        )
                        non_wildcards = cls.non_wildcard_qualifiers(template_qualifiers)
                        to_match = tuple(set(cls.qualifiers).difference(non_wildcards))
                        if len(to_match) > 1:
                            wildcard_match = False
                        else:
                            wildcard_match = source_format.is_subtype_of(to_match[0])
                    elif source_format.unqualified.is_subtype_of(
                        template_source_format.unqualified
                    ):
                        assert cls.wildcard_qualifiers(
                            template_qualifiers
                        ) == cls.wildcard_qualifiers(template_source_format.qualifiers)
                        if cls.ordered_qualifiers:
                            if len(cls.qualifiers) != len(template_qualifiers) or len(
                                source_format.qualifiers
                            ) != len(template_source_format.qualifiers):
                                wildcard_match = False
                            else:
                                wildcard_map = {}
                                for actual, template in zip(
                                    source_format.qualifiers,
                                    template_source_format.qualifiers,
                                ):
                                    if isinstance(template, ty.TypeVar):
                                        wildcard_map[template] = actual
                                for actual, template in zip(
                                    cls.qualifiers, template_qualifiers
                                ):
                                    if not actual.is_subtype_of(template):
                                        if not (
                                            isinstance(template, ty.TypeVar)
                                            and actual in wildcard_map
                                            and actual.is_subtype_of(
                                                wildcard_map[actual]
                                            )
                                        ):
                                            wildcard_match = False
                                            break
                        else:
                            non_wildcards = cls.non_wildcard_qualifiers(
                                template_qualifiers
                            )
                            src_non_wildcards = cls.non_wildcard_qualifiers(
                                template_source_format.qualifiers
                            )
                            if not non_wildcards.issubset(
                                set(cls.qualifiers)
                            ) or not src_non_wildcards.issubset(
                                set(source_format.qualifiers)
                            ):
                                wildcard_match = False
                            else:
                                to_match = set(cls.qualifiers).difference(non_wildcards)
                                from_types = set(source_format.qualifiers).difference(
                                    src_non_wildcards
                                )
                                wildcard_match = to_match.issubset(from_types)
                    if wildcard_match:
                        available_converters.append((converter, conv_kwargs))
        return available_converters

    @classmethod
    def is_subtype_of(cls, super_type: type):
        if super().is_subtype_of(super_type):
            return True
        # Check to see whether the unqualified types are equivalent
        if (
            not cls.is_qualified
            or not super_type.is_qualified
            or not cls.unqualified.is_subtype_of(super_type.unqualified)
        ):
            return False
        if cls.ordered_qualifiers:
            is_subtype = cls.qualifiers == super_type.qualifiers
        else:
            is_subtype = super_type.qualifiers.issubset(cls.qualifiers)
        return is_subtype

    @classmethod
    def register_converter(
        cls,
        source_format: type,
        converter_tuple: ty.Tuple[ty.Callable, ty.Dict[str, ty.Any]],
    ):
        """Registers a converter task within a class attribute. Called by the @fileformats.mark.converter
        decorator.

        Parameters
        ----------
        source_format : type
            the source format to register a converter from
        task_spec : ty.Callable
            a callable that resolves to a Pydra task
        converter_kwargs : dict
            additional keyword arguments to be passed to the task spec at initialisation
            time

        Raises
        ------
        FormatConversionError
            if there is already a converter registered between the two types
        """
        # Ensure "converters" dict is defined in the target class and not in a superclass
        if cls.wildcard_qualifiers():
            if isinstance(source_format, ty.TypeVar):
                if len(cls.wildcard_qualifiers()) > 1:
                    raise FileFormatsError(
                        "Can only have one wildcard qualifier when registering a converter "
                        f"to {cls} from a generic type, found {cls.wildcard_qualifiers()}"
                    )
            elif not source_format.is_qualified:
                raise FileFormatsError(
                    "Can only use wildcard qualifiers when registering a converter "
                    f"from a generic type or similarly qualified type, not {source_format}"
                )
            else:
                if cls.wildcard_qualifiers() != source_format.wildcard_qualifiers():
                    raise FileFormatsError(
                        f"Mismatching wildcards between source format, {source_format} "
                        f"({list(source_format.wildcard_qualifiers())}), and target "
                        f"{cls} ({cls.wildcard_qualifiers()})"
                    )
                prev_registered = (
                    f
                    for f in cls.converters
                    if f.non_wildcard_qualifiers()
                    == source_format.non_wildcard_qualifiers()
                )
                assert len(prev_registered) <= 1
                prev_registered = prev_registered[0]
                if prev_registered:
                    prev_registered_task = cls.converters[prev_registered][0]
                    msg = (
                        f"There is already a converter registered from {prev_registered.qualified} "
                        f"to {cls.qualified} with non-wildcard qualifiers "
                        f"{list(prev_registered.non_wilcard_qualifiers())}"
                    )
                    src_file = inspect.getsourcefile(prev_registered_task)
                    src_line = inspect.getsourcelines(prev_registered_task)[-1]
                    msg += f" (defined at line {src_line} of {src_file})"
                    raise FileFormatsError(msg)
            converters_dict = cls.get_converters_dict()
            converters_dict[source_format] = converter_tuple + (cls.qualifiers,)
        else:
            super().register_converter(source_format, converter_tuple)

    @classproperty
    def namespace(cls):
        """The "namespace" the format belongs to under the "fileformats" umbrella
        namespace"""
        if cls.is_qualified:
            namespaces = set(t.namespace for t in cls.qualifiers)
            if not cls.generically_qualified:
                namespaces.add(cls.unqualified.namespace)
            if len(namespaces) == 1:
                return next(iter(namespaces))
            else:
                msg = (
                    "Cannot create reversible MIME type for because did not find a "
                    f"common namespace between all qualifiers {list(cls.qualifiers)}"
                )
                if cls.generically_qualified:
                    msg += (
                        f" and (non genericly qualified) base class {cls.unqualified}"
                    )
                raise FileFormatsError(msg + f", found:\n{list(namespaces)}")
        else:
            namespace = super().namespace
        return namespace
