"""ctypes wrapper over libozonedb_pyshim.so (bench/usecases/mlflow/ozonedb_pyshim.cpp).

Only exposes what the MLflow metric-write bridge needs: open/put/get/
remove/close. Each OzoneDB() instance holds its own handle -- open one per
thread/process, matching the T/P convention documented in
PHASE_REPORT.md Phase 0 (each thread/process opens its own independent
DB* to the same on-disk directory; there is no internal cross-handle
locking to rely on instead).
"""
import ctypes
import os

_STATUS_NAMES = {0: "kSuccess", 1: "kFailure", 2: "kSealed", 3: "kNotFound"}


class OzoneDBError(Exception):
    def __init__(self, op, status):
        self.op = op
        self.status = status
        super().__init__(f"{op} failed: {_STATUS_NAMES.get(status, status)}")


def _load_lib():
    env_path = os.environ.get("OZONEDB_PYSHIM_PATH")
    candidates = [env_path] if env_path else []
    candidates.append(
        os.path.join(os.environ.get("OZONEDB_HOME", ""), "build", "libozonedb_pyshim.so")
    )
    for path in candidates:
        if path and os.path.exists(path):
            return ctypes.CDLL(path)
    raise FileNotFoundError(
        "libozonedb_pyshim.so not found -- set OZONEDB_PYSHIM_PATH or OZONEDB_HOME"
    )


_lib = _load_lib()

_lib.ozonedb_open.argtypes = [ctypes.c_char_p]
_lib.ozonedb_open.restype = ctypes.c_void_p

_lib.ozonedb_close.argtypes = [ctypes.c_void_p]
_lib.ozonedb_close.restype = None

_lib.ozonedb_put.argtypes = [
    ctypes.c_void_p, ctypes.c_char_p, ctypes.c_size_t,
    ctypes.c_char_p, ctypes.c_size_t,
]
_lib.ozonedb_put.restype = ctypes.c_int

_lib.ozonedb_remove.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_size_t]
_lib.ozonedb_remove.restype = ctypes.c_int

_lib.ozonedb_get.argtypes = [
    ctypes.c_void_p, ctypes.c_char_p, ctypes.c_size_t,
    ctypes.POINTER(ctypes.c_char_p), ctypes.POINTER(ctypes.c_size_t),
]
_lib.ozonedb_get.restype = ctypes.c_int

_lib.ozonedb_free_value.argtypes = [ctypes.c_char_p]
_lib.ozonedb_free_value.restype = None


class OzoneDB:
    def __init__(self, config_path: str):
        handle = _lib.ozonedb_open(str(config_path).encode())
        if not handle:
            raise OzoneDBError("open", 1)
        self._handle = handle
        self._closed = False

    def put(self, key: bytes, value: bytes) -> None:
        status = _lib.ozonedb_put(self._handle, key, len(key), value, len(value))
        if status != 0:
            raise OzoneDBError("put", status)

    def get(self, key: bytes) -> bytes:
        out_ptr = ctypes.c_char_p()
        out_len = ctypes.c_size_t()
        status = _lib.ozonedb_get(
            self._handle, key, len(key), ctypes.byref(out_ptr), ctypes.byref(out_len)
        )
        if status != 0:
            raise OzoneDBError("get", status)
        try:
            return ctypes.string_at(out_ptr, out_len.value)
        finally:
            _lib.ozonedb_free_value(out_ptr)

    def remove(self, key: bytes) -> None:
        status = _lib.ozonedb_remove(self._handle, key, len(key))
        if status != 0:
            raise OzoneDBError("remove", status)

    def close(self) -> None:
        if not self._closed:
            _lib.ozonedb_close(self._handle)
            self._closed = True

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
