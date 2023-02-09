import pytest
import pydra.mark
from fileformats.core import from_mime, DataType, FileSet
from fileformats.core.mark import converter
from fileformats.archive import Zip
from fileformats.generic import Directory
from fileformats.field import Array, Integer, Decimal, Text, Boolean
from fileformats.core.exceptions import (
    FileFormatsError,
    FormatConversionError,
    FormatRecognitionError,
    FormatMismatchError,
)
from fileformats.testing import (
    A,
    B,
    C,
    D,
    E,
    F,
    G,
    H,
    J,
    K,
    L,
    M,
    N,
    P,
    TestField,
)


SpecificDataType = DataType.type_var("SpecificDataType")
SpecificFileSet = FileSet.type_var("SpecificFileSet")


def test_qualified_equivalence():

    assert F is F
    assert F[A] is F[A]
    assert F[A, B] is F[B, A]
    assert K[A, B] is not K[B, A]  # ordered qualifers
    assert F[A] is not F[B]
    assert F[SpecificDataType] is F[SpecificDataType]
    assert F[SpecificDataType] is not F[A]
    assert F[SpecificDataType] is not F[SpecificFileSet]


def test_subtype_testing():

    assert G.is_subtype_of(F)
    assert not F.is_subtype_of(G)
    assert J.is_subtype_of(J)
    assert J.is_subtype_of(H)
    assert J[A].is_subtype_of(J)
    assert not H[A].is_subtype_of(F)
    assert not H[A].is_subtype_of(F[A])
    assert J[A, B].is_subtype_of(J[A])
    assert not J[B].is_subtype_of(J[A])
    assert not J[A].is_subtype_of(J[A, B])
    assert J[A].is_subtype_of(H[A])
    assert J[A, B, C].is_subtype_of(H[A, B])
    assert not J[A].is_subtype_of(H[B])
    assert FileSet.is_subtype_of(DataType)
    assert SpecificFileSet.is_subtype_of(SpecificDataType)
    assert F[SpecificFileSet].is_subtype_of(F[SpecificDataType])


def test_qualifier_fails():

    H[A, B, C]  # A, B, C are all allowable qualifier
    F[D]  # F has no restriction on qualifier types

    with pytest.raises(FileFormatsError) as e:
        H[D]
    assert "Invalid content types provided to" in str(e)

    with pytest.raises(FileFormatsError) as e:
        H[A, B, A]
    assert "Cannot have more than one occurrence of a qualifier" in str(e)

    K[A, B, A]  # ordered qualifiers allow repeats

    with pytest.raises(FileFormatsError) as e:
        L[A]
    assert (
        "Default value for qualifiers attribute 'new_qualifiers_attr' needs to be set"
        in str(e)
    )

    with pytest.raises(FileFormatsError) as e:
        M[A, B]
    assert "Multiple qualifiers not permitted for " in str(e)


def test_qualifier_converters():
    @converter
    @converter(source_format=F[A], target_format=H[A])
    @pydra.mark.task
    def f2h(in_file: F) -> H:
        return in_file

    H.get_converter(F)
    assert F.get_converter(G) is None  # G is subtype of F
    with pytest.raises(FormatConversionError):  # Cannot convert to more specific type
        G.get_converter(F)
    H[A].get_converter(F[A])
    assert F[A].get_converter(G[A]) is None
    assert F.get_converter(G[A]) is None
    with pytest.raises(FormatConversionError):
        F[A].get_converter(F[E])
    with pytest.raises(FormatConversionError):
        F[A].get_converter(G)


def test_mime_rountrips():

    assert Directory[F].mime_like == "testing/f+directory"
    assert from_mime("testing/f+directory") is Directory[F]

    assert Directory[H, F].mime_like == "testing/f.h+directory"
    assert from_mime("testing/f.h+directory") is Directory[F, H]

    with pytest.raises(FormatRecognitionError) as e:
        Array[TestField].mime_like
    assert "Cannot create reversible MIME type for " in str(e)


def test_inherited_qualifiers():

    assert Zip[G].mime_like == "testing/g+zip"
    assert from_mime("testing/g+zip") is Zip[G]


def test_arrays():

    Array[Integer]([1, 2, 3, 4])
    with pytest.raises(FormatMismatchError) as e:
        Array[Integer]([1.5, 2.2])
    assert "Cannot convert float (1.5) to integer field" in str(e)

    assert list(Array[Decimal]([1.5, 2.2])) == [1.5, 2.2]
    assert list(Array[Decimal](["1.5", "2.2"])) == [1.5, 2.2]
    assert list(Array[Decimal]("1.5, 2.2")) == [1.5, 2.2]
    assert list(Array[Decimal]("[1.5, 2.2]")) == [1.5, 2.2]
    assert list(Array[Text]("[1.5, 2.2]")) == ["1.5", "2.2"]

    assert list(Array[Boolean]("yes, no, 0, False, True, true")) == [
        True,
        False,
        False,
        False,
        True,
        True,
    ]

    assert list(from_mime("field/integer+array")("1,2,3,4,5")) == [1, 2, 3, 4, 5]


# Template-from-template conversions


@converter
@pydra.mark.task
def f2n_template(in_file: F[SpecificDataType]) -> N[SpecificDataType]:
    return in_file


@converter
@pydra.mark.task
def f2p_template(in_file: F[SpecificDataType]) -> P[SpecificDataType]:
    return in_file


@converter
@pydra.mark.task
def p2n_template(in_file: P[SpecificDataType]) -> N[SpecificDataType]:
    return in_file


def test_wildcard_template_from_template_conversion():
    H[A].get_converter(F[A])
    with pytest.raises(FormatConversionError):
        H[B].get_converter(F[A])


# Template from generic type to template


@converter
@pydra.mark.task
def generic2f(in_file: SpecificDataType) -> F[SpecificDataType]:
    return in_file


@converter
@pydra.mark.task
def generic2n(in_file: SpecificDataType) -> N[SpecificDataType, H]:
    return in_file


def test_wildcard_template_from_generic_conversion():

    F[J].get_converter(J)
    with pytest.raises(FormatConversionError):
        F[K].get_converter(J)

    N[J].get_converter(J)
    N[J, H].get_converter(J)
    with pytest.raises(FormatConversionError):
        F[K, H].get_converter(J)
    with pytest.raises(FormatConversionError):
        F[J, K].get_converter(J)


# Generic from template to  type


@converter
@pydra.mark.task
def f2generic(in_file: F[SpecificDataType]) -> SpecificDataType:
    return in_file


@converter
@pydra.mark.task
def n2generic(in_file: N[SpecificDataType, H]) -> SpecificDataType:
    return in_file


def test_wildcard_generic_from_template_conversion():
    J.get_converter(F[J])
    with pytest.raises(FormatConversionError):
        J.get_converter(F[K])


def test_wildcard_generic_from_multi_template_conversion():

    J.get_converter(N[J, H])
    with pytest.raises(FormatConversionError):
        J.get_converter(N[J, K])
