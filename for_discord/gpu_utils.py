"""
gpu_utils.py - CPU-only image processing utilities for feature extraction.

Provides optimized CPU implementations using OpenCV and SciPy.
All functions use single-threaded execution for resource-constrained environments.
"""

import numpy as np
import cv2
from typing import List, Sequence, Tuple, Optional
import logging

# Limit OpenCV to single thread
cv2.setNumThreads(1)
cv2.ocl.setUseOpenCL(False)

_MORPH_CROSS_3 = cv2.getStructuringElement(cv2.MORPH_CROSS, (3, 3))

logger = logging.getLogger(__name__)

# No GPU -- constants for compatibility
GPU_AVAILABLE = False
GPU_USABLE = False

# scipy.fft with workers=1 for single-threaded execution
try:
    from scipy.fft import fft2 as _scipy_fft2, ifft2 as _scipy_ifft2, fftshift as _scipy_fftshift
    _HAS_SCIPY_FFT = True
except ImportError:
    _HAS_SCIPY_FFT = False


def set_use_gpu(enabled: bool):
    """No-op. GPU is not available in this deployment."""
    pass


def get_gpu_status() -> dict:
    """Return CPU-only status."""
    return {
        'cupy_installed': False,
        'gpu_available': False,
        'gpu_usable': False,
        'gpu_usable_error': 'GPU support removed for resource-constrained deployment',
        'gpu_name': None,
        'gpu_memory_gb': 0,
        'cupy_version': None,
        'cuda_runtime_version': None,
        'cuda_driver_version': None,
        'gpu_enabled': False,
    }


# =========================================================================
# FFT operations (scipy single-threaded -> numpy fallback)
# =========================================================================

def gpu_fft2(arr: np.ndarray) -> np.ndarray:
    """2D FFT (single-threaded CPU)."""
    if _HAS_SCIPY_FFT:
        return _scipy_fft2(arr, workers=1)
    return np.fft.fft2(arr)


def gpu_ifft2(arr: np.ndarray) -> np.ndarray:
    """2D inverse FFT (single-threaded CPU)."""
    if _HAS_SCIPY_FFT:
        return _scipy_ifft2(arr, workers=1)
    return np.fft.ifft2(arr)


def gpu_fftshift(arr: np.ndarray) -> np.ndarray:
    """FFT shift."""
    return np.fft.fftshift(arr)


def gpu_fft2_shift(arr: np.ndarray) -> np.ndarray:
    """2D FFT + fftshift in one call."""
    if _HAS_SCIPY_FFT:
        return _scipy_fftshift(_scipy_fft2(arr, workers=1))
    return np.fft.fftshift(np.fft.fft2(arr))


def gpu_fft_magnitude_shifted(arr: np.ndarray) -> np.ndarray:
    """Magnitude of centered FFT: abs(fftshift(fft2(arr)))."""
    if _HAS_SCIPY_FFT:
        return np.abs(_scipy_fftshift(_scipy_fft2(arr, workers=1)))
    return np.abs(np.fft.fftshift(np.fft.fft2(arr)))


def gpu_fft_power_shifted(arr: np.ndarray) -> np.ndarray:
    """Power spectrum of centered FFT: abs(fftshift(fft2(arr)))**2."""
    if _HAS_SCIPY_FFT:
        f = _scipy_fftshift(_scipy_fft2(arr, workers=1))
    else:
        f = np.fft.fftshift(np.fft.fft2(arr))
    mag = np.abs(f)
    return mag * mag


def gpu_fft_magnitude_shifted_batch(arrays: np.ndarray) -> np.ndarray:
    """Batch FFT magnitude for arrays with shape (N, H, W)."""
    arrays = np.asarray(arrays)
    if arrays.ndim != 3:
        raise ValueError(f"Expected (N,H,W), got shape {arrays.shape}")
    if _HAS_SCIPY_FFT:
        fft = _scipy_fft2(arrays, axes=(-2, -1), workers=1)
        fft = _scipy_fftshift(fft, axes=(-2, -1))
        return np.abs(fft)
    fft = np.fft.fft2(arrays, axes=(-2, -1))
    fft = np.fft.fftshift(fft, axes=(-2, -1))
    return np.abs(fft)


# =========================================================================
# Spatial filters (cv2 fast path -> scipy fallback)
# =========================================================================

def gpu_gaussian_filter_multi(arr: np.ndarray, sigmas: Sequence[float], mode: str = 'reflect') -> List[np.ndarray]:
    """Apply multiple Gaussian filters to the same input."""
    from scipy import ndimage
    return [ndimage.gaussian_filter(arr, sigma=float(s), mode=mode) for s in sigmas]


def gpu_convolve(arr: np.ndarray, kernel: np.ndarray, mode: str = 'reflect') -> np.ndarray:
    """2D convolution."""
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
    """Uniform (box) filter."""
    if arr.ndim == 2:
        try:
            return cv2.blur(arr, (size, size), borderType=cv2.BORDER_REFLECT)
        except Exception:
            pass
    from scipy import ndimage
    return ndimage.uniform_filter(arr, size=size)


def gpu_gaussian_filter(arr: np.ndarray, sigma: float) -> np.ndarray:
    """Gaussian filter."""
    if arr.ndim == 2:
        try:
            ksize = int(6 * sigma + 1) | 1
            if arr.dtype == np.float32:
                return cv2.GaussianBlur(arr, (ksize, ksize), sigma)
            else:
                return cv2.GaussianBlur(arr.astype(np.float32), (ksize, ksize), sigma).astype(arr.dtype)
        except Exception:
            pass
    from scipy import ndimage
    return ndimage.gaussian_filter(arr, sigma=sigma)


def gpu_sobel(arr: np.ndarray, axis: int) -> np.ndarray:
    """Sobel filter."""
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


def gpu_sobel_xy(arr: np.ndarray) -> Tuple[np.ndarray, np.ndarray]:
    """Sobel gradients along both axes. Returns (gx, gy)."""
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


def gpu_maximum_filter(arr: np.ndarray, size: int) -> np.ndarray:
    """Maximum filter."""
    from scipy import ndimage
    return ndimage.maximum_filter(arr, size=size)


def gpu_minimum_filter(arr: np.ndarray, size: int) -> np.ndarray:
    """Minimum filter."""
    from scipy import ndimage
    return ndimage.minimum_filter(arr, size=size)


def gpu_local_variance(arr: np.ndarray, size: int) -> np.ndarray:
    """Local variance: E[X^2] - E[X]^2."""
    arr_f = arr.astype(np.float64)
    mean = gpu_uniform_filter(arr_f, size)
    mean_sq = gpu_uniform_filter(arr_f * arr_f, size)
    return np.maximum(mean_sq - mean * mean, 0)


def gpu_local_contrast(arr: np.ndarray, size: int) -> np.ndarray:
    """Local contrast (max - min)."""
    from scipy import ndimage
    return ndimage.maximum_filter(arr, size=size) - ndimage.minimum_filter(arr, size=size)


def gpu_correlate2d_fft(arr1: np.ndarray, arr2: Optional[np.ndarray] = None) -> np.ndarray:
    """2D correlation using FFT. If arr2 is None, computes autocorrelation."""
    arr1_centered = arr1 - np.mean(arr1)
    fft1 = gpu_fft2(arr1_centered)

    if arr2 is None:
        power = fft1.real ** 2 + fft1.imag ** 2
        result = gpu_ifft2(power).real
    else:
        arr2_centered = arr2 - np.mean(arr2)
        fft2_result = gpu_fft2(arr2_centered)
        result = gpu_ifft2(fft1 * np.conj(fft2_result)).real
    return np.fft.fftshift(result)


def gpu_laplacian(arr: np.ndarray) -> np.ndarray:
    """Laplacian filter."""
    from scipy import ndimage
    return ndimage.laplace(arr)


def gpu_binary_dilation(arr: np.ndarray, iterations: int = 1) -> np.ndarray:
    """Binary dilation."""
    mask_u8 = arr.astype(np.uint8) if arr.dtype != np.uint8 else arr
    return cv2.dilate(mask_u8, _MORPH_CROSS_3, iterations=iterations).astype(bool)


def gpu_binary_opening(arr: np.ndarray, iterations: int = 1) -> np.ndarray:
    """Binary opening."""
    mask_u8 = arr.astype(np.uint8) if arr.dtype != np.uint8 else arr
    return cv2.morphologyEx(mask_u8, cv2.MORPH_OPEN, _MORPH_CROSS_3, iterations=iterations).astype(bool)


def gpu_binary_closing(arr: np.ndarray, iterations: int = 1) -> np.ndarray:
    """Binary closing."""
    mask_u8 = arr.astype(np.uint8) if arr.dtype != np.uint8 else arr
    return cv2.morphologyEx(mask_u8, cv2.MORPH_CLOSE, _MORPH_CROSS_3, iterations=iterations).astype(bool)


def gpu_median_filter(arr: np.ndarray, size: int) -> np.ndarray:
    """Median filter."""
    from scipy import ndimage
    return ndimage.median_filter(arr, size=size)


def gpu_dct2(arr: np.ndarray, norm='ortho') -> np.ndarray:
    """2D DCT."""
    from scipy import fftpack
    return fftpack.dct(
        fftpack.dct(arr, axis=-1, norm=norm),
        axis=-2, norm=norm
    )


def gpu_batch_fft2(arrays: list) -> list:
    """Batch 2D FFT for multiple arrays."""
    return [gpu_fft2(arr) for arr in arrays]


# =========================================================================
# FAST LBP (Local Binary Pattern)
# =========================================================================

_UNIFORM_LBP_LUT = np.zeros(256, dtype=np.uint8)
for _v in range(256):
    _ones = bin(_v).count('1')
    _bits = [(_v >> _i) & 1 for _i in range(8)]
    _trans = sum(_bits[_i] != _bits[(_i + 1) % 8] for _i in range(8))
    _UNIFORM_LBP_LUT[_v] = _ones if _trans <= 2 else 9


def fast_lbp_uniform(img_u8: np.ndarray) -> np.ndarray:
    """
    Fast vectorized LBP (radius=1, 8 points, uniform method).
    Returns array of shape (H-2, W-2) with values 0-9.
    """
    g = img_u8.astype(np.float32)
    h, w = g.shape
    center = g[1:h - 1, 1:w - 1]

    W_FAR = np.float32(0.5)
    W_MID = np.float32(0.20710678)
    W_NEAR = np.float32(0.08578644)

    pattern = np.zeros((h - 2, w - 2), dtype=np.uint8)

    # Cardinal neighbors
    pattern |= (g[1:h - 1, 2:w] >= center).view(np.uint8)
    pattern |= ((g[0:h - 2, 1:w - 1] >= center).view(np.uint8) << 2)
    pattern |= ((g[1:h - 1, 0:w - 2] >= center).view(np.uint8) << 4)
    pattern |= ((g[2:h, 1:w - 1] >= center).view(np.uint8) << 6)

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
