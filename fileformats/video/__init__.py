from ..core import __version__
from ..core.mixin import WithMagicNumber
from fileformats.generic import File


class Video(File):
    "Base class for audio file formats"
    binary = True
    iana_mime = None


class Mp4(Video):
    """"""

    iana_mime = "video/mp4"
    ext = ".mp4"
    alternative_exts = (".mpg4",)


class Webm(Video):
    ext = ".webm"
    iana_mime = "video/webm"


class Quicktime(Video):
    ext = ".mov"
    alternate_exts = (".qt",)
    iana_mime = "video/quicktime"


class Ogg(WithMagicNumber, File):
    """"""

    iana_mime = "video/ogg"
    ext = ".ogv"
    magic_number = b"OggS"


class _1d_interleaved_parityfec(Video):
    """"""

    iana_mime = "video/1d-interleaved-parityfec"
    ext = None


class _3gpp(Video):
    """TODO: None.  However, the file-type box
    must occur first in the file, and
    MUST contain a 3GPP brand in its
    compatible brands list."""

    iana_mime = "video/3gpp"
    ext = ".3gp"
    alternative_exts = (".3gpp",)


class _3gpp2(Video):
    """TODO: the file-type box must occur first in the file
    and MUST contain a 3GPP2 brand in its compatible brands list."""

    iana_mime = "video/3gpp2"
    ext = None


class _3gpp_tt(Video):
    """"""

    iana_mime = "video/3gpp-tt"
    ext = None


class Av1(Video):
    """"""

    iana_mime = "video/AV1"
    ext = None


class Bmpeg(Video):
    """"""

    iana_mime = "video/BMPEG"
    ext = None


class Bt656(Video):
    """"""

    iana_mime = "video/BT656"
    ext = None


class Celb(Video):
    """"""

    iana_mime = "video/CelB"
    ext = None


class Dv(Video):
    """"""

    iana_mime = "video/DV"
    ext = None


class Encaprtp(Video):
    """"""

    iana_mime = "video/encaprtp"
    ext = None


class Example(Video):
    """"""

    iana_mime = "video/example"
    ext = None


class Ffv1(Video):
    """"""

    iana_mime = "video/FFV1"
    ext = None


class Flexfec(Video):
    """"""

    iana_mime = "video/flexfec"
    ext = None


class H261(Video):
    """"""

    iana_mime = "video/H261"
    ext = None


class H263(Video):
    """"""

    iana_mime = "video/H263"
    ext = None


class H263_1998(Video):
    """"""

    iana_mime = "video/H263-1998"
    ext = None


class H263_2000(Video):
    """"""

    iana_mime = "video/H263-2000"
    ext = None


class H264(Video):
    """"""

    iana_mime = "video/H264"
    ext = None


class H264_rcdo(Video):
    """"""

    iana_mime = "video/H264-RCDO"
    ext = None


class H264_svc(Video):
    """"""

    iana_mime = "video/H264-SVC"
    ext = None


class H265(Video):
    """"""

    iana_mime = "video/H265"
    ext = None


class H266(Video):
    """"""

    iana_mime = "video/H266"
    ext = None


class Iso___segment(Video):
    """"""

    iana_mime = "video/iso.segment"
    ext = ".m4s"


class Jpeg(Video):
    """"""

    iana_mime = "video/JPEG"
    ext = None


class Jpeg2000(Video):
    """"""

    iana_mime = "video/jpeg2000"
    ext = None


class Jxsv(Video):
    """"""

    iana_mime = "video/jxsv"
    ext = None


class Mj2(WithMagicNumber, File):
    """"""

    iana_mime = "video/mj2"
    ext = ".mj2"
    alternative_exts = (".mjp2",)
    magic_number = "c6a5020200d0a870a"


class Mp1s(Video):
    """"""

    iana_mime = "video/MP1S"
    ext = None


class Mp2p(Video):
    """"""

    iana_mime = "video/MP2P"
    ext = None


class Mp2t(Video):
    """"""

    iana_mime = "video/MP2T"
    ext = None


class Mp4v_es(Video):
    """"""

    iana_mime = "video/MP4V-ES"
    ext = None


class Mpv(Video):
    """"""

    iana_mime = "video/MPV"
    ext = None


class Mpeg4_generic(Video):
    """"""

    iana_mime = "video/mpeg4-generic"
    ext = None


class Nv(Video):
    """"""

    iana_mime = "video/nv"
    ext = None


class Parityfec(Video):
    """"""

    iana_mime = "video/parityfec"
    ext = None


class Pointer(Video):
    """"""

    iana_mime = "video/pointer"
    ext = None


class Raptorfec(Video):
    """"""

    iana_mime = "video/raptorfec"
    ext = None


class Raw(Video):
    """"""

    iana_mime = "video/raw"
    ext = None


class Rtp_enc_aescm128(Video):
    """"""

    iana_mime = "video/rtp-enc-aescm128"
    ext = None


class Rtploopback(Video):
    """"""

    iana_mime = "video/rtploopback"
    ext = None


class Rtx(Video):
    """"""

    iana_mime = "video/rtx"
    ext = None


class Scip(Video):
    """"""

    iana_mime = "video/scip"
    ext = None


class Smpte291(Video):
    """"""

    iana_mime = "video/smpte291"
    ext = None


class Smpte292m(Video):
    """"""

    iana_mime = "video/SMPTE292M"
    ext = None


class Ulpfec(Video):
    """"""

    iana_mime = "video/ulpfec"
    ext = None


class Vc1(Video):
    """"""

    iana_mime = "video/vc1"
    ext = None


class Vc2(Video):
    """"""

    iana_mime = "video/vc2"
    ext = None


class Vp8(Video):
    """"""

    iana_mime = "video/VP8"
    ext = None


class Vp9(Video):
    """"""

    iana_mime = "video/VP9"
    ext = None
