from store import Store

def serve() -> str:
    return Store().read()
