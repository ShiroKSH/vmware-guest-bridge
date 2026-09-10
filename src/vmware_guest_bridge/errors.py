class BridgeError(Exception):
    exit_code = 1

    def __init__(self, message, *, code="operation_failed", **details):
        super().__init__(message)
        self.code = code
        self.details = details

    def as_dict(self):
        return {"code": self.code, "message": str(self), **self.details}


class InvalidRequest(BridgeError):
    exit_code = 2

    def __init__(self, message):
        super().__init__(message, code="invalid_request")


class OutcomeUnknown(BridgeError):
    exit_code = 3

    def __init__(self, message, **details):
        super().__init__(message, code="outcome_unknown", **details)
