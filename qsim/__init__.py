"""QSim: point-in-time multi-factor research backtesting."""
__version__ = "0.2.0"

def run(config):
    from .runner import run as _run
    return _run(config)
