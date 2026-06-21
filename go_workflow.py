import importlib.util
import sys
from pathlib import Path


_PACKAGE_DIR = Path(__file__).resolve().parent / "go_workflow_extension" / "go_workflow"
_PACKAGE_INIT = _PACKAGE_DIR / "__init__.py"
_PACKAGE_NAME = "_go_workflow_runtime"


def _load_runtime_module():
    existing = sys.modules.get(_PACKAGE_NAME)
    if existing is not None:
        return existing

    spec = importlib.util.spec_from_file_location(
        _PACKAGE_NAME,
        _PACKAGE_INIT,
        submodule_search_locations=[str(_PACKAGE_DIR)],
    )
    if spec is None or spec.loader is None:
        raise ImportError(f"无法加载 Go Workflow 运行包: {_PACKAGE_INIT}")

    module = importlib.util.module_from_spec(spec)
    sys.modules[_PACKAGE_NAME] = module
    spec.loader.exec_module(module)
    return module


_RUNTIME = _load_runtime_module()

for _name in dir(_RUNTIME):
    if _name.startswith("__") and _name not in {"__version__", "__all__"}:
        continue
    globals()[_name] = getattr(_RUNTIME, _name)


__all__ = getattr(_RUNTIME, "__all__", [name for name in globals() if not name.startswith("_")])
