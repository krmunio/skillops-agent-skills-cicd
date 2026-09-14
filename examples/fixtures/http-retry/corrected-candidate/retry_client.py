TRANSIENT_STATUSES = {502, 503, 504}
IDEMPOTENT_METHODS = {"GET", "HEAD", "PUT", "DELETE", "OPTIONS"}


def request(method, service, path, max_retries=2):
    attempts = 0
    response = service.request(method, path)
    while (
        method.upper() in IDEMPOTENT_METHODS
        and response["status"] in TRANSIENT_STATUSES
        and attempts < max_retries
    ):
        attempts += 1
        response = service.request(method, path)
    return response
