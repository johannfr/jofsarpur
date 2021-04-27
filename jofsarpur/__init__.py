from importlib.metadata import version

try:
    __version__ = version(__name__)
except:
    __version__ = "0.1.0"
