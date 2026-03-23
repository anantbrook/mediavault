class MediaVaultException(Exception):
    """Base class for all exceptions in MediaVault."""
    pass

class FileNotFoundError(MediaVaultException):
    """Raised when a file is not found."""
    def __init__(self, message="File not found."):
        self.message = message
        super().__init__(self.message)

class PermissionDeniedError(MediaVaultException):
    """Raised when permission is denied."""
    def __init__(self, message="Permission denied."):
        self.message = message
        super().__init__(self.message)

class InvalidFileFormatError(MediaVaultException):
    """Raised when the file format is invalid."""
    def __init__(self, message="Invalid file format."):
        self.message = message
        super().__init__(self.message)

# Add more custom exceptions as needed