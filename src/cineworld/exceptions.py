class CineworldError(RuntimeError):
    """Base wrapper error."""


class CineworldHTTPError(CineworldError):
    def __init__(self, status: int, message: str, payload=None):
        super().__init__(f"Cineworld HTTP {status}: {message}")
        self.status = status
        self.payload = payload


class AuthenticationError(CineworldError):
    """Cineworld rejected the login or returned an auth error."""


class LoginTimeoutError(AuthenticationError):
    """The interactive browser login did not complete before timeout."""


class NotAuthenticatedError(AuthenticationError):
    """An authenticated endpoint was called without a Cineworld session."""
