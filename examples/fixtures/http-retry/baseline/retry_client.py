def request(method, service, path, max_retries=2):
    return service.request(method, path)
