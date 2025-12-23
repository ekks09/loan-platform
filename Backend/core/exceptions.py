# core/exceptions.py
from rest_framework.views import exception_handler

def custom_exception_handler(exc, context):
    response = exception_handler(exc, context)
    if response is not None:
        response.data = {"error": response.data}
    else:
        response = Response({"error": "Server error"}, status=500)
    return response
