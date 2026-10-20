"""Public errors deliberately omit provider bodies, headers and credentials."""


class ClientError(Exception):
    def __init__(self, code: str, message: str, *, retryable: bool = False, retry_after: float | None = None):
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message
        self.retryable = retryable
        self.retry_after = retry_after


def status_error(status: int, retry_after: float | None = None) -> ClientError:
    if status in {401, 403}:
        return ClientError("authentication", "provider rejected credentials or permissions")
    if status == 429:
        return ClientError("rate_limit", "provider rate limit reached", retryable=True, retry_after=retry_after)
    if status >= 500:
        return ClientError("provider_unavailable", "provider service unavailable", retryable=True)
    return ClientError("invalid_request", f"provider rejected request (HTTP {status}); check model and capabilities")
