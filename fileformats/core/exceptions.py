class FileFormatError(Exception):
    pass


class FileFormatConversionError(Exception):
    "No converters exist between formats"


class FilePathsNotSetException(Exception):
    pass
