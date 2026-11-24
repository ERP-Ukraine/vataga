import importlib
from pathlib import Path
import sys
import types

ROOT = Path(__file__).resolve().parents[2]
PACKAGE = '_vataga_portable'
if PACKAGE not in sys.modules:
    package = types.ModuleType(PACKAGE)
    package.__path__ = [str(ROOT)]
    sys.modules[PACKAGE] = package

policy = importlib.import_module(PACKAGE + '.policy')
bridge = importlib.import_module(PACKAGE + '.workflow_bridge')
internal = importlib.import_module(PACKAGE + '.internal')
