class GetYYError(Exception):
    """Base application error."""


class ValidationError(GetYYError):
    """Raised for invalid user input."""


class GORequestError(GetYYError):
    """Raised when the GO report cannot be loaded."""


class MESRequestError(GetYYError):
    """Raised when the MES report cannot be loaded."""


class YPDAuthenticationError(GetYYError):
    """Raised when YPD credentials are rejected."""


class YPDRequestError(GetYYError):
    """Raised when the YPD report cannot be loaded."""


class DatabaseQueryError(GetYYError):
    """Raised when PPO lookup fails."""


class PPORequestError(GetYYError):
    """Raised when the PPO report cannot be loaded."""


class WorkbookFormatError(GetYYError):
    """Raised when a workbook cannot be round-tripped."""


class ParseError(GetYYError):
    """Raised when expected report content cannot be parsed."""
