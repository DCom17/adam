"""Shared rate limiter. Lives in its own module so the router modules and
server.py can use the same Limiter without a circular import."""

from slowapi import Limiter
from slowapi.util import get_remote_address

limiter = Limiter(key_func=get_remote_address)
