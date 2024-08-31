from abc import ABCMeta
import typing as ty
import logging
from .utils import describe_task, matching_source
from .exceptions import FormatDefinitionError
from .mixin import WithClassifiers

if ty.TYPE_CHECKING:
    from .datatype import DataType
    from pydra.engine.task import TaskBase
    from pydra.engine import Workflow
    from .fileset import FileSet

logger = logging.getLogger("fileformats")

TaskGenerator = ty.Callable[..., "TaskBase"]


class ConverterWrapper:
    """Wraps a converter task in a workflow so that the in_file and out_file names can
    be mapped onto their standardised names, "in_file" and "out_file" if necessary
    """

    task_spec: TaskGenerator
    in_file: str
    out_file: str

    def __init__(
        self,
        task_spec: TaskGenerator,
        in_file: str,
        out_file: str,
    ):
        self.task_spec = task_spec
        self.in_file = in_file
        self.out_file = out_file

    def __repr__(self) -> str:
        return f"{self.__class__.__name__}({self.task_spec}, {self.in_file}, {self.out_file})"

    def __call__(
        self, name: ty.Optional[str] = None, **kwargs: ty.Dict[str, ty.Any]
    ) -> "Workflow":
        from pydra.engine import Workflow

        if name is None:
            name = f"{self.task_spec.__name__}_wrapper"
        wf = Workflow(name=name, input_spec=["in_file"])
        task_kwargs = {self.in_file: wf.lzin.in_file}
        task_kwargs.update(kwargs)
        wf.add(self.task_spec(name="task", **task_kwargs))
        wf.set_output([("out_file", getattr(wf.task.lzout, self.out_file))])
        return wf


DT = ty.TypeVar("DT", bound=DataType)


class SubtypeVar:
    """To handle the case where the target format is a placeholder (type-var) defined by
    by its relationship to the source format, e.g.

    AnyFileFormat = FileSet.type_var("AnyFileFormat")

    @converter
    @pydra.mark.task
    def unzip(in_file: Zip[AnyFileFormat], out_file: AnyFileFormat):
        ...
    """

    converters: ty.Dict[
        ty.Type["FileSet"], ty.Tuple[TaskGenerator, ty.Dict[str, ty.Any]]
    ] = {}

    @classmethod
    def new(cls, name: str, klass: type) -> "SubtypeVar":
        """Create a new subtype

        Parameters
        ----------
        name : str
            name for the subtype
        klass : ty.Type[DataType]
            the class to sub-type

        Returns
        -------
        SubtypeVar
            a sub-type that is
        """
        return ABCMeta(name, (cls, klass), {"bound": klass})  # type: ignore

    @classmethod
    def __subclasshook__(cls, subclass: type) -> bool:
        if issubclass(subclass, SubtypeVar):
            return issubclass(subclass.bound, cls.bound)  # type: ignore
        return type.__subclasscheck__(cls, subclass)

    @classmethod
    def get_converter_tuples(
        cls, source_format: ty.Type[WithClassifiers], target_format: type
    ) -> ty.List[ty.Tuple[ty.Callable[..., TaskBase], ty.Dict[str, ty.Any]]]:
        # check to see whether there are converters from a base class of the source
        # format
        available_converters: ty.List[
            ty.Tuple[ty.Callable[..., TaskBase], ty.Dict[str, ty.Any]]
        ] = []
        # assert isinstance(source_format, WithClassifiers)
        if source_format.is_classified:
            for template_source_format, converter in cls.converters.items():
                assert isinstance(template_source_format, WithClassifiers)
                if not issubclass(
                    template_source_format.unclassified, source_format.unclassified
                ):
                    continue
                assert len(template_source_format.wildcard_classifiers()) == 1
                non_wildcards = template_source_format.non_wildcard_classifiers()
                if not non_wildcards.issubset(source_format.classifiers):
                    continue
                from_types = tuple(
                    set(source_format.classifiers).difference(non_wildcards)
                )
                if any(issubclass(q, target_format) for q in from_types):
                    available_converters.append(converter)
        return available_converters

    @classmethod
    def register_converter(
        cls,
        source_format: ty.Type[WithClassifiers],
        converter_tuple: ty.Tuple[ty.Callable[..., TaskBase], ty.Dict[str, ty.Any]],
    ) -> None:
        """Registers a converter task within a class attribute. Called by the
        @fileformats.core.converter decorator.

        Parameters
        ----------
        source_format : type
            the source format to register a converter from
        converter_tuple
            a tuple consisting of a `task_spec` callable that resolves to a Pydra task
            and a dictionary of keyword arguments to be passed to the task spec at
            initialisation time

        Raises
        ------
        FormatConversionError
            if there is already a converter registered between the two types
        """
        # Ensure "converters" dict is defined in the target class and not in a superclass
        if len(source_format.wildcard_classifiers()) > 1:
            raise FormatDefinitionError(
                "Cannot register a conversion to a generic type from a type with more "
                f"than one wildcard {source_format} ({list(source_format.wildcard_classifiers())})"
            )
        prev_registered = [
            f
            for f in cls.converters
            if (
                f.unclassified is source_format.unclassified  # type: ignore
                and f.non_wildcard_classifiers()  # type: ignore
                == source_format.non_wildcard_classifiers()
            )
        ]
        assert len(prev_registered) <= 1
        if prev_registered:
            prev_tuple = cls.converters[prev_registered[0]]
            task, task_kwargs = converter_tuple
            prev_task, prev_kwargs = prev_tuple
            if matching_source(task, prev_task) and task_kwargs == prev_kwargs:
                logger.warning(
                    "Ignoring duplicate registrations of the same converter %s",
                    describe_task(task),
                )
                return  # actually the same task but just imported twice for some reason
            generic_type = tuple(prev_task.wildcard_classifiers())[0]  # type: ignore
            raise FormatDefinitionError(
                f"Cannot register converter from {source_format} to the generic type "
                f"'{generic_type}', {describe_task(task)} "
                f"because there is already one registered, {describe_task(prev_task)}"
            )

        cls.converters[source_format] = converter_tuple  # type: ignore
