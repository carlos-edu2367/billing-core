class DomainError(Exception):
    pass

class InvalidEmailError(DomainError):
    """Raised when an email is invalid."""
    pass

class InvalidCPFError(DomainError):
    pass

class InvalidCNPJError(DomainError):
    pass

class NotFoundError(DomainError):
    pass


class UnsupportedGatewayError(DomainError):
    pass
