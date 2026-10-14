from .adapters import HTTPModelProvider


def create_provider(profile, secrets, *, transport=None, oauth=None):
    return HTTPModelProvider(profile, secrets, transport=transport, oauth=oauth)
