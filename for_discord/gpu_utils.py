"""
gpu_utils.py - GPU acceleration utilities for feature extraction.

Provides optional GPU acceleration using CuPy (drop-in NumPy replacement).
Falls back to NumPy/SciPy if CuPy is not available.

Install CuPy for your CUDA version:
    pip install cupy-cuda13x  # For CUDA 13.x
    pip install cupy-cuda12x  # For CUDA 12.x
    pip install cupy-cuda11x  # For CUDA 11.x
"""

import numpy as np
import os
import cv2
from typing import Iterable, List, Sequence, Tuple, Optional
import logging

_MORPH_CROSS_3 = cv2.getStructuringElement(cv2.MORPH_CROSS, (3, 3))

logger = logging.getLogger(__name__)

# Try to import CuPy for GPU acceleration
try:
    import cupy as cp
    from cupyx.scipy import ndimage as cp_ndimage
    from cupyx.scipy import fft as cp_fft
    HAS_CUPY = True

    # Check if CUDA is actually available and libraries can be loaded
    try:
        # Try to actually use CuPy to detect runtime issues
        _ = cp.array([1.0])
        cp.cuda.runtime.getDeviceCount()
        GPU_AVAILABLE = True
        GPU_NAME = cp.cuda.runtime.getDeviceProperties(0)['name'].decode()
        GPU_MEMORY = cp.cuda.runtime.getDeviceProperties(0)['totalGlobalMem'] / (1024**3)
        CUPY_VERSION = getattr(cp, "__version__", None)
        try:
            CUDA_RUNTIME_VERSION = int(cp.cuda.runtime.runtimeGetVersion())
        except Exception:
            CUDA_RUNTIME_VERSION = None
        try:
            CUDA_DRIVER_VERSION = int(cp.cuda.runtime.driverGetVersion())
        except Exception:
            CUDA_DRIVER_VERSION = None
        logger.info(f"GPU available: {GPU_NAME} ({GPU_MEMORY:.1f} GB)")
    except Exception as e:
        GPU_AVAILABLE = False
        GPU_NAME = None
        GPU_MEMORY = 0
        CUPY_VERSION = None
        CUDA_RUNTIME_VERSION = None
        CUDA_DRIVER_VERSION = None
        error_msg = str(e)
        if 'nvrtc' in error_msg.lower() or 'dll' in error_msg.lower():
            logger.warning(f"CuPy installed but CUDA libraries not found. Install CUDA Toolkit or use --no-gpu flag.")
        else:
            logger.warning(f"CuPy installed but no GPU available: {e}")
except ImportError:
    HAS_CUPY = False
    GPU_AVAILABLE = False
    GPU_NAME = None
    GPU_MEMORY = 0
    CUPY_VERSION = None
    CUDA_RUNTIME_VERSION = None
    CUDA_DRIVER_VERSION = None

# "Available" means a CUDA device exists and CuPy can load its runtime.
# "Usable" means kernels can compile/run (ndimage/fft self-test passes).
GPU_USABLE = False
GPU_USABLE_ERROR: Optional[str] = None
_GPU_SELF_TEST_RAN = False


def _detect_cupy_distribution() -> Optional[str]:
    try:
        from importlib import metadata
    except Exception:
        return None

    # Prefer explicit CUDA builds first.
    for dist in ("cupy-cuda13x", "cupy-cuda12x", "cupy-cuda11x", "cupy"):
        try:
            ver = metadata.version(dist)
        except metadata.PackageNotFoundError:
            continue
        except Exception:
            continue
        return f"{dist}=={ver}"

    return None


def _run_gpu_self_test() -> None:
    """
    Validate that GPU execution is actually usable (not just "device exists").

    We intentionally hit:
    - a trivial ufunc (kernel launch),
    - a cupyx.scipy.ndimage op (often requires NVRTC),
    - and synchronize to make failures deterministic.
    """
    global GPU_USABLE, GPU_USABLE_ERROR, _GPU_SELF_TEST_RAN
    if _GPU_SELF_TEST_RAN:
        return
    _GPU_SELF_TEST_RAN = True

    GPU_USABLE = False
    GPU_USABLE_ERROR = None

    if not (HAS_CUPY and GPU_AVAILABLE):
        GPU_USABLE = False
        GPU_USABLE_ERROR = "CuPy not installed or CUDA device not available"
        return

    try:
        x = cp.arange(16, dtype=cp.float32).reshape(4, 4)
        _ = cp.abs(x)  # ufunc/kernel launch

        img = cp.zeros((64, 64), dtype=cp.float32)
        _ = cp_ndimage.gaussian_filter(img, sigma=1.5, mode="reflect")

        cp.cuda.Stream.null.synchronize()
        GPU_USABLE = True
        GPU_USABLE_ERROR = None
    except Exception as e:
        GPU_USABLE = False
        GPU_USABLE_ERROR = str(e)

        cuda_path = os.environ.get("CUDA_PATH") or ""
        cupy_dist = _detect_cupy_distribution() or "<unknown>"
        logger.warning(
            "GPU detected but not usable for CuPy kernels; falling back to CPU. "
            f"CUDA_PATH={cuda_path!r} cupy={CUPY_VERSION!r} dist={cupy_dist} "
            f"runtime={CUDA_RUNTIME_VERSION!r} driver={CUDA_DRIVER_VERSION!r} err={e}"
        )


# Global flag to enable/disable GPU in wrappers below.
# Default OFF; callers (FeatureExtractor/bench) should opt in.
_use_gpu = False


def set_use_gpu(enabled: bool):
    """Enable or disable GPU acceleration."""
    global _use_gpu
    if not enabled:
        _use_gpu = False
        return

    if not GPU_AVAILABLE:
        logger.warning("GPU requested but not available, using CPU")
        _use_gpu = False
        return

    _run_gpu_self_test()
    if not GPU_USABLE:
        logger.warning("GPU requested but CuPy kernels are not usable, using CPU")
        _use_gpu = False
        return

    _use_gpu = True


def get_gpu_status() -> dict:
    """Get GPU status information."""
    # Make status reflect "usable" rather than just "device exists".
    if HAS_CUPY and GPU_AVAILABLE:
        _run_gpu_self_test()
    return {
        'cupy_installed': HAS_CUPY,
        'gpu_available': GPU_AVAILABLE,
        'gpu_usable': GPU_USABLE,
        'gpu_usable_error': GPU_USABLE_ERROR,
        'gpu_name': GPU_NAME,
        'gpu_memory_gb': GPU_MEMORY,
        'cupy_version': CUPY_VERSION,
        'cuda_runtime_version': CUDA_RUNTIME_VERSION,
        'cuda_driver_version': CUDA_DRIVER_VERSION,
        'gpu_enabled': _use_gpu,
    }


def to_gpu(arr: np.ndarray) -> 'cp.ndarray':
    """Transfer array to GPU if available, otherwise return as-is."""
    if _use_gpu and HAS_CUPY and GPU_USABLE:
        return cp.asarray(arr)
    return arr


def to_cpu(arr) -> np.ndarray:
    """Transfer array to CPU if on GPU, otherwise return as-is."""
    if HAS_CUPY and isinstance(arr, cp.ndarray):
        return cp.asnumpy(arr)
    return arr


def get_array_module(arr):
    """Get the appropriate array module (cupy or numpy) for the array."""
    if HAS_CUPY and isinstance(arr, cp.ndarray):
        return cp
    return np


# GPU-accelerated operations with automatic CPU fallback on error

# Try to use MKL FFT for faster CPU-side FFT
try:
    import mkl_fft
    _HAS_MKL_FFT = True
except ImportError:
    _HAS_MKL_FFT = False

# scipy.fft with workers=-1 uses all cores — 3-4x faster than numpy.fft
try:
    from scipy.fft import fft2 as _scipy_fft2, ifft2 as _scipy_ifft2, fftshift as _scipy_fftshift
    _HAS_SCIPY_FFT = True
except ImportError:
    _HAS_SCIPY_FFT = False

def gpu_fft2(arr: np.ndarray) -> np.ndarray:
    """2D FFT with optional GPU acceleration, scipy.fft(workers=-1) fallback."""
    if _use_gpu and HAS_CUPY and GPU_USABLE:
        try:
            arr_gpu = cp.asarray(arr)
            result = cp.fft.fft2(arr_gpu)
            return cp.asnumpy(result)
        except Exception:
            pass
    if _HAS_SCIPY_FFT:
        return _scipy_fft2(arr, workers=-1)
    if _HAS_MKL_FFT:
        try:
            return mkl_fft.fft2(arr)
        except Exception:
            pass
    return np.fft.fft2(arr)

def gpu_ifft2(arr: np.ndarray) -> np.ndarray:
    """2D inverse FFT with optional GPU acceleration, scipy.fft(workers=-1) fallback."""
    if _use_gpu and HAS_CUPY and GPU_USABLE:
        try:
            arr_gpu = cp.asarray(arr)
            result = cp.fft.ifft2(arr_gpu)
            return cp.asnumpy(result)
        except Exception:
            pass
    if _HAS_SCIPY_FFT:
        return _scipy_ifft2(arr, workers=-1)
    return np.fft.ifft2(arr)


def gpu_fftshift(arr: np.ndarray) -> np.ndarray:
    """FFT shift with optional GPU acceleration."""
    if _use_gpu and HAS_CUPY and GPU_USABLE:
        try:
            arr_gpu = cp.asarray(arr)
            result = cp.fft.fftshift(arr_gpu)
            return cp.asnumpy(result)
        except Exception:
            # Fall back to CPU if GPU fails
            pass
    return np.fft.fftshift(arr)


def gpu_fft2_shift(arr: np.ndarray) -> np.ndarray:
    """
    2D FFT + fftshift in one call.

    This reduces CPU↔GPU transfer overhead vs calling gpu_fft2() then gpu_fftshift().
    Returns a complex ndarray on CPU (NumPy).
    """
    if _use_gpu and HAS_CUPY and GPU_USABLE:
        try:
            arr_gpu = cp.asarray(arr)
            result = cp.fft.fftshift(cp.fft.fft2(arr_gpu))
            return cp.asnumpy(result)
        except Exception:
            pass
    if _HAS_SCIPY_FFT:
        return _scipy_fftshift(_scipy_fft2(arr, workers=-1))
    return np.fft.fftshift(np.fft.fft2(arr))


def gpu_fft_magnitude_shifted(arr: np.ndarray) -> np.ndarray:
    """
    Magnitude of centered FFT (abs(fftshift(fft2(arr)))).
    """
    if _use_gpu and HAS_CUPY and GPU_USABLE:
        try:
            arr_gpu = cp.asarray(arr)
            mag = cp.abs(cp.fft.fftshift(cp.fft.fft2(arr_gpu)))
            return cp.asnumpy(mag)
        except Exception:
            pass
    if _HAS_SCIPY_FFT:
        return np.abs(_scipy_fftshift(_scipy_fft2(arr, workers=-1)))
    return np.abs(np.fft.fftshift(np.fft.fft2(arr)))


def gpu_fft_power_shifted(arr: np.ndarray) -> np.ndarray:
    """Power spectrum of centered FFT (abs(fftshift(fft2(arr)))**2)."""
    if _use_gpu and HAS_CUPY and GPU_USABLE:
        try:
            arr_gpu = cp.asarray(arr)
            mag = cp.abs(cp.fft.fftshift(cp.fft.fft2(arr_gpu)))
            power = mag * mag
            return cp.asnumpy(power)
        except Exception:
            pass
    if _HAS_SCIPY_FFT:
        f = _scipy_fftshift(_scipy_fft2(arr, workers=-1))
    else:
        f = np.fft.fftshift(np.fft.fft2(arr))
    mag = np.abs(f)
    return mag * mag


def gpu_fft_magnitude_shifted_batch(arrays: np.ndarray) -> np.ndarray:
    """
    Batch FFT magnitude for arrays with shape (N, H, W).

    Uses a single stacked FFT on GPU (axes=-2,-1) to reduce launch/transfer
    overhead.
    """
    arrays = np.asarray(arrays)
    if arrays.ndim != 3:
        raise ValueError(f"gpu_fft_magnitude_shifted_batch expects (N,H,W), got shape {arrays.shape}")

    if _use_gpu and HAS_CUPY and GPU_USABLE:
        try:
            arr_gpu = cp.asarray(arrays)
            fft = cp.fft.fft2(arr_gpu, axes=(-2, -1))
            fft = cp.fft.fftshift(fft, axes=(-2, -1))
            mag = cp.abs(fft)
            return cp.asnumpy(mag)
        except Exception:
            pass

    if _HAS_SCIPY_FFT:
        fft = _scipy_fft2(arrays, axes=(-2, -1), workers=-1)
        fft = _scipy_fftshift(fft, axes=(-2, -1))
        return np.abs(fft)

    fft = np.fft.fft2(arrays, axes=(-2, -1))
    fft = np.fft.fftshift(fft, axes=(-2, -1))
    return np.abs(fft)


def gpu_gaussian_filter_multi(arr: np.ndarray, sigmas: Sequence[float], mode: str = 'reflect') -> List[np.ndarray]:
    """
    Apply multiple Gaussian filters to the same input with a single CPU→GPU transfer.

    Returns a list of NumPy arrays (one per sigma), preserving current feature
    code expectations.
    """
    sigmas_f = [float(s) for s in sigmas]
    if _use_gpu and HAS_CUPY and GPU_USABLE:
        try:
            arr_gpu = cp.asarray(arr)
            outs: List[np.ndarray] = []
            for sigma in sigmas_f:
                out_gpu = cp_ndimage.gaussian_filter(arr_gpu, sigma=sigma, mode=mode)
                outs.append(cp.asnumpy(out_gpu))
            return outs
        except Exception:
            pass

    from scipy import ndimage
    return [ndimage.gaussian_filter(arr, sigma=sigma, mode=mode) for sigma in sigmas_f]


def gpu_convolve(arr: np.ndarray, kernel: np.ndarray, mode: str = 'reflect') -> np.ndarray:
    """2D convolution with optional GPU acceleration."""
    if _use_gpu and HAS_CUPY and GPU_USABLE:
        try:
            arr_gpu = cp.asarray(arr)
            kernel_gpu = cp.asarray(kernel)
            result = cp_ndimage.convolve(arr_gpu, kernel_gpu, mode=mode)
            return cp.asnumpy(result)
        except Exception:
            pass
    # cv2.filter2D does correlation; flip kernel for convolution equivalence
    if arr.ndim == 2:
        try:
            _BORDER_MAP = {'reflect': cv2.BORDER_REFLECT, 'constant': cv2.BORDER_CONSTANT,
                           'nearest': cv2.BORDER_REPLICATE, 'wrap': cv2.BORDER_WRAP}
            border = _BORDER_MAP.get(mode, cv2.BORDER_REFLECT)
            kflip = kernel[::-1, ::-1].copy()
            return cv2.filter2D(arr, -1, kflip, borderType=border)
        except Exception:
            pass
    from scipy import ndimage
    return ndimage.convolve(arr, kernel, mode=mode)


def gpu_uniform_filter(arr: np.ndarray, size: int) -> np.ndarray:
    """Uniform filter with optional GPU acceleration."""
    if _use_gpu and HAS_CUPY and GPU_USABLE:
        try:
            arr_gpu = cp.asarray(arr)
            result = cp_ndimage.uniform_filter(arr_gpu, size=size)
            return cp.asnumpy(result)
        except Exception:
            pass
    # cv2.blur is ~7x faster than scipy uniform_filter, identical with BORDER_REFLECT
    if arr.ndim == 2:
        try:
            return cv2.blur(arr, (size, size), borderType=cv2.BORDER_REFLECT)
        except Exception:
            pass
    from scipy import ndimage
    return ndimage.uniform_filter(arr, size=size)


def gpu_gaussian_filter(arr: np.ndarray, sigma: float) -> np.ndarray:
    """Gaussian filter with optional GPU acceleration."""
    if _use_gpu and HAS_CUPY and GPU_USABLE:
        try:
            arr_gpu = cp.asarray(arr)
            result = cp_ndimage.gaussian_filter(arr_gpu, sigma=sigma)
            return cp.asnumpy(result)
        except Exception:
            # Fall back to CPU if GPU fails
            pass
    # Use cv2 GaussianBlur (~10x faster than scipy) for 2D arrays
    if arr.ndim == 2:
        try:
            ksize = int(6 * sigma + 1) | 1  # Ensure odd kernel size
            if arr.dtype == np.float32:
                return cv2.GaussianBlur(arr, (ksize, ksize), sigma)
            else:
                return cv2.GaussianBlur(arr.astype(np.float32), (ksize, ksize), sigma).astype(arr.dtype)
        except Exception:
            pass
    from scipy import ndimage
    return ndimage.gaussian_filter(arr, sigma=sigma)


def gpu_sobel(arr: np.ndarray, axis: int) -> np.ndarray:
    """Sobel filter with optional GPU acceleration."""
    if _use_gpu and HAS_CUPY and GPU_USABLE:
        try:
            arr_gpu = cp.asarray(arr)
            result = cp_ndimage.sobel(arr_gpu, axis=axis)
            return cp.asnumpy(result)
        except Exception:
            # Fall back to CPU if GPU fails
            pass
    # cv2.Sobel is ~15x faster than scipy.ndimage.sobel (identical interior values)
    if arr.ndim == 2:
        try:
            a32 = arr.astype(np.float32) if arr.dtype != np.float32 else arr
            if axis == 0:
                result = cv2.Sobel(a32, cv2.CV_32F, 0, 1, ksize=3)
            else:
                result = cv2.Sobel(a32, cv2.CV_32F, 1, 0, ksize=3)
            return result.astype(arr.dtype) if arr.dtype != np.float32 else result
        except Exception:
            pass
    from scipy import ndimage
    return ndimage.sobel(arr, axis=axis)


def gpu_maximum_filter(arr: np.ndarray, size: int) -> np.ndarray:
    """Maximum filter with optional GPU acceleration."""
    if _use_gpu and HAS_CUPY and GPU_USABLE:
        try:
            arr_gpu = cp.asarray(arr)
            result = cp_ndimage.maximum_filter(arr_gpu, size=size)
            return cp.asnumpy(result)
        except Exception:
            # Fall back to CPU if GPU fails
            pass
    from scipy import ndimage
    return ndimage.maximum_filter(arr, size=size)


def gpu_minimum_filter(arr: np.ndarray, size: int) -> np.ndarray:
    """Minimum filter with optional GPU acceleration."""
    if _use_gpu and HAS_CUPY and GPU_USABLE:
        try:
            arr_gpu = cp.asarray(arr)
            result = cp_ndimage.minimum_filter(arr_gpu, size=size)
            return cp.asnumpy(result)
        except Exception:
            # Fall back to CPU if GPU fails
            pass
    from scipy import ndimage
    return ndimage.minimum_filter(arr, size=size)


def gpu_local_variance(arr: np.ndarray, size: int) -> np.ndarray:
    """
    Fast local variance computation with optional GPU acceleration.
    Uses: var = E[X²] - E[X]²
    """
    if _use_gpu and HAS_CUPY and GPU_USABLE:
        try:
            arr_gpu = cp.asarray(arr.astype(np.float64))
            mean = cp_ndimage.uniform_filter(arr_gpu, size=size)
            mean_sq = cp_ndimage.uniform_filter(arr_gpu * arr_gpu, size=size)
            local_var = cp.maximum(mean_sq - mean * mean, 0)
            return cp.asnumpy(local_var)
        except Exception:
            pass

    arr_f = arr.astype(np.float64)
    mean = gpu_uniform_filter(arr_f, size)
    mean_sq = gpu_uniform_filter(arr_f * arr_f, size)
    return np.maximum(mean_sq - mean * mean, 0)


def gpu_local_contrast(arr: np.ndarray, size: int) -> np.ndarray:
    """
    Fast local contrast (max - min) with optional GPU acceleration.
    """
    if _use_gpu and HAS_CUPY and GPU_USABLE:
        try:
            arr_gpu = cp.asarray(arr)
            max_val = cp_ndimage.maximum_filter(arr_gpu, size=size)
            min_val = cp_ndimage.minimum_filter(arr_gpu, size=size)
            return cp.asnumpy(max_val - min_val)
        except Exception:
            # Fall back to CPU if GPU fails
            pass

    from scipy import ndimage
    return ndimage.maximum_filter(arr, size=size) - ndimage.minimum_filter(arr, size=size)


def gpu_correlate2d_fft(arr1: np.ndarray, arr2: Optional[np.ndarray] = None) -> np.ndarray:
    """
    2D correlation using FFT (much faster than spatial correlation).
    If arr2 is None, computes autocorrelation (single FFT path).
    """
    is_autocorr = arr2 is None

    if _use_gpu and HAS_CUPY and GPU_USABLE:
        try:
            arr1_gpu = cp.asarray(arr1 - np.mean(arr1))

            fft1 = cp.fft.fft2(arr1_gpu)
            if is_autocorr:
                power = fft1.real ** 2 + fft1.imag ** 2  # |fft1|^2 avoids conj alloc
                result = cp.fft.ifft2(power).real
            else:
                arr2_gpu = cp.asarray(arr2 - np.mean(arr2))
                fft2 = cp.fft.fft2(arr2_gpu)
                result = cp.fft.ifft2(fft1 * cp.conj(fft2)).real
            result = cp.fft.fftshift(result)
            return cp.asnumpy(result)
        except Exception:
            # Fall back to CPU if GPU fails
            pass

    arr1_centered = arr1 - np.mean(arr1)
    fft1 = gpu_fft2(arr1_centered)

    if is_autocorr:
        power = fft1.real ** 2 + fft1.imag ** 2  # |fft1|^2
        result = gpu_ifft2(power).real
    else:
        arr2_centered = arr2 - np.mean(arr2)
        fft2_result = gpu_fft2(arr2_centered)
        result = gpu_ifft2(fft1 * np.conj(fft2_result)).real
    return np.fft.fftshift(result)


def gpu_sobel_xy(arr: np.ndarray) -> Tuple[np.ndarray, np.ndarray]:
    """
    Compute Sobel gradients along both axes in one call.
    Returns (gx, gy) where gx=axis=1 (horizontal), gy=axis=0 (vertical).
    More efficient than two separate gpu_sobel calls (single GPU transfer).
    """
    if _use_gpu and HAS_CUPY and GPU_USABLE:
        try:
            arr_gpu = cp.asarray(arr)
            gx = cp_ndimage.sobel(arr_gpu, axis=1)
            gy = cp_ndimage.sobel(arr_gpu, axis=0)
            return cp.asnumpy(gx), cp.asnumpy(gy)
        except Exception:
            pass
    # cv2.Sobel is ~15x faster than scipy.ndimage.sobel
    if arr.ndim == 2:
        try:
            a32 = arr.astype(np.float32) if arr.dtype != np.float32 else arr
            gx = cv2.Sobel(a32, cv2.CV_32F, 1, 0, ksize=3)
            gy = cv2.Sobel(a32, cv2.CV_32F, 0, 1, ksize=3)
            if arr.dtype != np.float32:
                return gx.astype(arr.dtype), gy.astype(arr.dtype)
            return gx, gy
        except Exception:
            pass
    from scipy import ndimage
    return ndimage.sobel(arr, axis=1), ndimage.sobel(arr, axis=0)


def gpu_laplacian(arr: np.ndarray) -> np.ndarray:
    """Laplacian filter with optional GPU acceleration."""
    if _use_gpu and HAS_CUPY and GPU_USABLE:
        try:
            arr_gpu = cp.asarray(arr)
            result = cp_ndimage.laplace(arr_gpu)
            return cp.asnumpy(result)
        except Exception:
            pass
    from scipy import ndimage
    return ndimage.laplace(arr)


def gpu_binary_dilation(arr: np.ndarray, iterations: int = 1) -> np.ndarray:
    """Binary dilation with optional GPU acceleration."""
    if _use_gpu and HAS_CUPY and GPU_USABLE:
        try:
            arr_gpu = cp.asarray(arr.astype(np.bool_))
            result = cp_ndimage.binary_dilation(arr_gpu, iterations=iterations)
            return cp.asnumpy(result)
        except Exception:
            pass
    mask_u8 = arr.astype(np.uint8) if arr.dtype != np.uint8 else arr
    return cv2.dilate(mask_u8, _MORPH_CROSS_3, iterations=iterations).astype(bool)


def gpu_binary_opening(arr: np.ndarray, iterations: int = 1) -> np.ndarray:
    """Binary opening with optional GPU acceleration."""
    if _use_gpu and HAS_CUPY and GPU_USABLE:
        try:
            arr_gpu = cp.asarray(arr.astype(np.bool_))
            result = cp_ndimage.binary_opening(arr_gpu, iterations=iterations)
            return cp.asnumpy(result)
        except Exception:
            pass
    mask_u8 = arr.astype(np.uint8) if arr.dtype != np.uint8 else arr
    return cv2.morphologyEx(mask_u8, cv2.MORPH_OPEN, _MORPH_CROSS_3, iterations=iterations).astype(bool)


def gpu_binary_closing(arr: np.ndarray, iterations: int = 1) -> np.ndarray:
    """Binary closing with optional GPU acceleration."""
    if _use_gpu and HAS_CUPY and GPU_USABLE:
        try:
            arr_gpu = cp.asarray(arr.astype(np.bool_))
            result = cp_ndimage.binary_closing(arr_gpu, iterations=iterations)
            return cp.asnumpy(result)
        except Exception:
            pass
    mask_u8 = arr.astype(np.uint8) if arr.dtype != np.uint8 else arr
    return cv2.morphologyEx(mask_u8, cv2.MORPH_CLOSE, _MORPH_CROSS_3, iterations=iterations).astype(bool)


def gpu_median_filter(arr: np.ndarray, size: int) -> np.ndarray:
    """Median filter with optional GPU acceleration."""
    if _use_gpu and HAS_CUPY and GPU_USABLE:
        try:
            arr_gpu = cp.asarray(arr)
            result = cp_ndimage.median_filter(arr_gpu, size=size)
            return cp.asnumpy(result)
        except Exception:
            pass
    from scipy import ndimage
    return ndimage.median_filter(arr, size=size)


def gpu_dct2(arr: np.ndarray, norm='ortho') -> np.ndarray:
    """
    2D DCT with optional GPU acceleration.
    Performs DCT along both axes. Significantly faster on GPU for large arrays.

    Args:
        arr: Input array (can be 2D or 3D for batch processing)
        norm: Normalization mode ('ortho' or None)

    Returns:
        DCT-transformed array (same shape as input)
    """
    if _use_gpu and HAS_CUPY and GPU_USABLE:
        try:
            from cupyx.scipy import fftpack as cp_fftpack

            arr_gpu = cp.asarray(arr)
            # DCT along last two axes for 2D/3D arrays
            result = cp_fftpack.dct(
                cp_fftpack.dct(arr_gpu, axis=-1, norm=norm),
                axis=-2, norm=norm
            )
            return cp.asnumpy(result)
        except Exception as e:
            logger.debug(f"GPU DCT failed, using CPU: {e}")
            # Fall back to CPU if GPU fails
            pass

    # CPU fallback
    from scipy import fftpack
    return fftpack.dct(
        fftpack.dct(arr, axis=-1, norm=norm),
        axis=-2, norm=norm
    )


# Batch operations for efficiency
def gpu_batch_fft2(arrays: list) -> list:
    """Batch 2D FFT for multiple arrays."""
    if _use_gpu and HAS_CUPY and GPU_USABLE:
        try:
            # Stack to do a single batched FFT kernel launch.
            arrs = np.asarray(arrays)
            arr_gpu = cp.asarray(arrs)
            result_gpu = cp.fft.fft2(arr_gpu, axes=(-2, -1))
            result = cp.asnumpy(result_gpu)
            # Return list to preserve original API.
            return [result[i] for i in range(result.shape[0])]
        except Exception:
            # Fall back to CPU if GPU fails
            pass
    return [np.fft.fft2(arr) for arr in arrays]


def print_gpu_status():
    """Print GPU status information."""
    status = get_gpu_status()
    print("\n" + "=" * 50)
    print("GPU Status")
    print("=" * 50)
    
    if status['gpu_available']:
        print(f"✅ GPU Available: {status['gpu_name']}")
        print(f"   Memory: {status['gpu_memory_gb']:.1f} GB")
        print(f"   Enabled: {'Yes' if status['gpu_enabled'] else 'No'}")
    else:
        print("❌ No GPU available")
        if not status['cupy_installed']:
            print("   Install CuPy: pip install cupy-cuda12x")
    
    print("=" * 50 + "\n")


# =========================================================================
# FAST LBP (Local Binary Pattern)
# =========================================================================

# Lookup table for uniform LBP (radius=1, 8 points)
_UNIFORM_LBP_LUT = np.zeros(256, dtype=np.uint8)
for _v in range(256):
    _ones = bin(_v).count('1')
    _bits = [(_v >> _i) & 1 for _i in range(8)]
    _trans = sum(_bits[_i] != _bits[(_i + 1) % 8] for _i in range(8))
    _UNIFORM_LBP_LUT[_v] = _ones if _trans <= 2 else 9


def fast_lbp_uniform(img_u8: np.ndarray) -> np.ndarray:
    """
    Fast vectorized LBP (radius=1, 8 points, uniform method).
    
    Matches skimage.feature.local_binary_pattern(img, 8, 1, method='uniform')
    using bilinear interpolation at diagonal positions.
    Returns array of shape (H-2, W-2) with values 0-9.
    ~2.5x faster than skimage.
    """
    g = img_u8.astype(np.float32)
    h, w = g.shape
    center = g[1:h - 1, 1:w - 1]

    # Bilinear interpolation weights for sin(45°)=cos(45°)=0.7071
    W_FAR = np.float32(0.5)          # 0.7071 * 0.7071
    W_MID = np.float32(0.20710678)   # 0.7071 * 0.2929
    W_NEAR = np.float32(0.08578644)  # 0.2929 * 0.2929

    pattern = np.zeros((h - 2, w - 2), dtype=np.uint8)

    # Cardinal neighbors (exact integer positions)
    pattern |= (g[1:h - 1, 2:w] >= center).view(np.uint8)           # bit 0: E
    pattern |= ((g[0:h - 2, 1:w - 1] >= center).view(np.uint8) << 2)  # bit 2: N
    pattern |= ((g[1:h - 1, 0:w - 2] >= center).view(np.uint8) << 4)  # bit 4: W
    pattern |= ((g[2:h, 1:w - 1] >= center).view(np.uint8) << 6)      # bit 6: S

    # Diagonal neighbors (bilinear interpolation)
    n_NE = W_MID * g[0:h - 2, 1:w - 1] + W_FAR * g[0:h - 2, 2:w] + W_NEAR * center + W_MID * g[1:h - 1, 2:w]
    pattern |= ((n_NE >= center).view(np.uint8) << 1)

    n_NW = W_FAR * g[0:h - 2, 0:w - 2] + W_MID * g[0:h - 2, 1:w - 1] + W_MID * g[1:h - 1, 0:w - 2] + W_NEAR * center
    pattern |= ((n_NW >= center).view(np.uint8) << 3)

    n_SW = W_MID * g[1:h - 1, 0:w - 2] + W_NEAR * center + W_FAR * g[2:h, 0:w - 2] + W_MID * g[2:h, 1:w - 1]
    pattern |= ((n_SW >= center).view(np.uint8) << 5)

    n_SE = W_NEAR * center + W_MID * g[1:h - 1, 2:w] + W_MID * g[2:h, 1:w - 1] + W_FAR * g[2:h, 2:w]
    pattern |= ((n_SE >= center).view(np.uint8) << 7)

    return _UNIFORM_LBP_LUT[pattern]


if __name__ == '__main__':
    print_gpu_status()
    
    # Quick benchmark
    if GPU_AVAILABLE:
        import time
        
        # Test array
        arr = np.random.rand(512, 512).astype(np.float64)
        
        # Warm up
        _ = gpu_fft2(arr)
        
        # Benchmark FFT
        set_use_gpu(False)
        start = time.time()
        for _ in range(10):
            _ = gpu_fft2(arr)
        cpu_time = time.time() - start
        
        set_use_gpu(True)
        start = time.time()
        for _ in range(10):
            _ = gpu_fft2(arr)
        gpu_time = time.time() - start
        
        print(f"FFT (512x512, 10 iterations):")
        print(f"  CPU: {cpu_time:.3f}s")
        print(f"  GPU: {gpu_time:.3f}s")
        print(f"  Speedup: {cpu_time/gpu_time:.1f}x")
