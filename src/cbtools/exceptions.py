class CbtoolsError(Exception):
    """Base class for all exceptions raised by cbtools."""
    pass

class InvalidArgumentError(CbtoolsError):
    """Raised when the arguments passed to a function are invalid."""
    pass

class ParseError(CbtoolsError):
    """Raised when a parsing error occurs."""
    pass

class MissingDependencyError(CbtoolsError):
    """Raised when a required dependency is missing."""
    pass

class UnsupportedFileTypeError(CbtoolsError):
    """Raised when an unsupported file is encountered."""
    pass

class SubprocessError(CbtoolsError):
    """Raised when a subprocess command fails."""
    pass

class FileError(CbtoolsError):
    """Raised when a file is not found."""
    pass
