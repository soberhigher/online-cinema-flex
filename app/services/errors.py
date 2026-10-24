from typing import Any


class ServiceError(Exception):
    """Business rule violation; turned into an HTTP error response in app.main."""

    def __init__(self, status_code: int, detail: Any) -> None:
        self.status_code = status_code
        self.detail = detail
        super().__init__(str(detail))
