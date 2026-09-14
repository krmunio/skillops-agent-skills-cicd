def request(method, service, path, max_retries=2):
    response = service.request(method, path)
    while response["status"] >= 500:
        response = service.request(method, path)
    return response
