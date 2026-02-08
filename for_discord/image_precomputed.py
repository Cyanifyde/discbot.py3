"""
image_precomputed.py - Lazy-computed shared data to eliminate redundant computations.

This class provides a centralized place to compute commonly used image transformations
once and reuse them across multiple feature extraction modules, significantly reducing
redundant computations.

OPTIMIZATION v2:
- Extended with FFT (full complex + power spectrum)
- Local variance maps
- Gaussian filtered versions (multiple sigmas)
- Color channels and color space conversions
- Noise residual estimation
"""

import numpy as np
from scipy import ndimage
from typing import Optional, Tuple
import logging

logger = logging.getLogger(__name__)

# GPU-accelerated operations (with automatic CPU fallback)
try:
    from gpu_utils import (
        gpu_fft2 as _fft2, gpu_fftshift as _fftshift, gpu_fft2_shift as _fft2_shift,
        gpu_ifft2 as _ifft2,
        gpu_gaussian_filter, gpu_sobel, gpu_sobel_xy,
        gpu_laplacian, gpu_uniform_filter, gpu_local_variance,
        gpu_binary_dilation,
    )
    _USING_GPU_UTILS = True
except ImportError:
    # Fallback to scipy/numpy
    try:
        from scipy.fft import fft2 as _scipy_fft2, ifft2 as _scipy_ifft2, fftshift as _scipy_fftshift
        _fft2 = lambda arr: _scipy_fft2(arr, workers=-1)
        _ifft2 = lambda arr: _scipy_ifft2(arr, workers=-1)
        _fftshift = _scipy_fftshift
    except ImportError:
        from numpy.fft import fft2 as _fft2, ifft2 as _ifft2, fftshift as _fftshift
    _USING_GPU_UTILS = False
    _fft2_shift = lambda arr: _fftshift(_fft2(arr))

# Optional OpenCV acceleration
try:
    import cv2
    HAS_CV2 = True
except ImportError:
    HAS_CV2 = False


class ImagePrecomputedData:
    """
    Lazy-computed precomputed image data to share across feature extraction modules.

    This class computes and caches commonly used image transformations only when they
    are first accessed, avoiding redundant computations across different feature modules.

    Usage:
        precomputed = ImagePrecomputedData(img_array)
        gray = precomputed.gray  # Computed once, cached
        edges = precomputed.edges  # Uses cached gray

    Attributes (lazy-computed properties):
        # Basic
        img_array: Original image array (H, W, 3) or (H, W)
        gray: Grayscale conversion (H, W) as float64
        gray_uint8: Grayscale as uint8 for functions requiring it

        # Gradients
        sobel_h: Horizontal Sobel gradient (axis=0, Y derivative)
        sobel_v: Vertical Sobel gradient (axis=1, X derivative)
        edges: Sobel edge magnitude
        edge_binary: Thresholded binary edge map (90th percentile)

        # FFT
        fft_complex: Full complex FFT (shifted)
        fft_magnitude: FFT magnitude
        fft_power: FFT power spectrum (magnitude^2)
        fft_log_magnitude: Log of FFT magnitude

        # Local statistics
        local_mean: Local mean (5x5 window)
        local_variance: Local variance (5x5 window)

        # Filtered versions
        gaussian_0_5: Gaussian filtered (sigma=0.5)
        gaussian_1: Gaussian filtered (sigma=1)
        gaussian_1p5: Gaussian filtered (sigma=1.5)
        gaussian_2: Gaussian filtered (sigma=2)
        gaussian_3: Gaussian filtered (sigma=3)
        gaussian_5: Gaussian filtered (sigma=5)

        # Second derivatives
        laplacian: Laplacian of grayscale

        # High-pass
        high_pass: High-pass filtered (gray - gaussian_1)

        # Color channels
        r, g, b: Color channels as float64

        # Color spaces
        hsv: HSV color space
        ycbcr: YCbCr color space
        lab: LAB color space

        # Noise
        noise_residual: Estimated noise residual
    """

    def __init__(self, img_array: np.ndarray):
        """
        Initialize with an image array.

        Args:
            img_array: RGB image as numpy array (H, W, 3) or grayscale (H, W)
        """
        self._img_array = img_array
        self._is_color = img_array.ndim == 3 and img_array.shape[2] >= 3
        self._gpu_preloaded = False

        # Cached values (initialized to None, computed on first access)
        # Basic
        self._gray: Optional[np.ndarray] = None
        self._gray_uint8: Optional[np.ndarray] = None

        # Gradients
        self._sobel_h: Optional[np.ndarray] = None
        self._sobel_v: Optional[np.ndarray] = None
        self._edges: Optional[np.ndarray] = None
        self._edge_binary: Optional[np.ndarray] = None

        # FFT
        self._fft_complex: Optional[np.ndarray] = None
        self._fft_magnitude: Optional[np.ndarray] = None
        self._fft_power: Optional[np.ndarray] = None
        self._fft_log_magnitude: Optional[np.ndarray] = None
        self._fft_phase: Optional[np.ndarray] = None  # angle of unshifted FFT

        # Filtered versions
        self._gaussian_0_5: Optional[np.ndarray] = None
        self._gaussian_1: Optional[np.ndarray] = None
        self._gaussian_1p5: Optional[np.ndarray] = None
        self._gaussian_2: Optional[np.ndarray] = None
        self._gaussian_3: Optional[np.ndarray] = None
        self._gaussian_5: Optional[np.ndarray] = None

        # Laplacian
        self._laplacian: Optional[np.ndarray] = None

        # High-pass (for checkerboard detection, texture analysis)
        self._high_pass: Optional[np.ndarray] = None

        # Color channels
        self._r: Optional[np.ndarray] = None
        self._g: Optional[np.ndarray] = None
        self._b: Optional[np.ndarray] = None

        # Color spaces
        self._hsv: Optional[np.ndarray] = None
        self._ycbcr: Optional[np.ndarray] = None
        self._lab: Optional[np.ndarray] = None

        # Noise
        self._noise_residual: Optional[np.ndarray] = None

        # Gradient direction
        self._gradient_direction: Optional[np.ndarray] = None

        # Local std
        self._local_std: Optional[np.ndarray] = None

        # Global gray statistics
        self._gray_mean: Optional[float] = None
        self._gray_var: Optional[float] = None
        self._gray_std: Optional[float] = None

        # Histogram
        self._gray_histogram: Optional[np.ndarray] = None
        self._gray_histogram_raw: Optional[np.ndarray] = None
        self._gray_histogram_cumsum: Optional[np.ndarray] = None
        self._channel_histograms: Optional[Dict[str, np.ndarray]] = None  # {'r','g','b'} -> 256 float64

        # Edge dilated (for near-edge analysis)
        self._edge_dilated: Optional[np.ndarray] = None
        
        # Gradient direction
        self._gradient_direction: Optional[np.ndarray] = None
        
        # Residuals (commonly used)
        self._residual_1p5: Optional[np.ndarray] = None
        self._residual_2: Optional[np.ndarray] = None

        # General-purpose caches for arbitrary parameters
        self._gaussian_cache = {}  # (sigma, mode) -> ndarray
        # Gaussian of gray**2, used by NSS/MSCN (sigma_local estimation)
        self._gaussian_sq_cache = {}  # (sigma, mode) -> ndarray
        self._uniform_cache = {}   # (size, mode) -> ndarray
        self._local_variance_cache = {}  # (size, mode) -> ndarray
        self._residual_cache = {}  # sigma -> ndarray
        self._fft_residual_cache = {}  # sigma -> fft of residual
        self._channel_residual_cache = {}  # (channel_idx, sigma) -> ndarray
        # Sobel of Gaussian-blurred gray, used by brush multiscale features.
        # Keyed by (sigma, mode) where sigma is the blur sigma applied before Sobel.
        self._sobel_gaussian_cache = {}  # (sigma, mode) -> (sobel_v, sobel_h)
        
        # Downsampled versions for expensive operations on large images
        self._gray_small: Optional[np.ndarray] = None
        self._img_small: Optional[np.ndarray] = None
        self._small_scale: float = 1.0  # Scale factor from original to small

        # Blockgrid boundary diffs (cached, used by 6+ callers)
        self._line_boundary_diffs: Optional[tuple] = None

        # Face detection cache (computed once, used by anatomical + advanced)
        self._face_detections = None  # Will be np.ndarray or empty tuple
        self._face_cascade_loaded = False

        # Autocorrelation caches (costly FFT-based)
        self._autocorr_gray: Optional[np.ndarray] = None
        self._autocorr_texture: Optional[np.ndarray] = None

        # Eye and body detection caches
        self._eye_detections = None
        self._eye_cascade_loaded = False
        self._body_detections = None
        self._body_cascade_loaded = False

        # Wavelet decomposition caches
        self._wavelet_haar = None
        self._wavelet_haar_multi = None
        self._wavelet_db4 = None

        # Half-resolution FFT cache (for cross-scale phase analysis)
        self._fft_half = None

        # Canny edges + HoughLinesP cache (used by physics perspective + horizon)
        self._canny_edges_50_150 = None
        self._hough_lines_sensitive = None  # threshold=50, minLen=30

    def preload_gpu_common(self) -> bool:
        """
        Precompute commonly reused transforms on GPU once per image.

        The goal is to avoid repeated CPU->GPU transfers for each operation:
        transfer grayscale once, run shared transforms on GPU, then populate the
        existing CPU caches so feature families can reuse them.

        Returns:
            True if GPU preloading ran (or was already done), else False.
        """
        if self._gpu_preloaded:
            return True

        # Only run when GPU is explicitly enabled and verified usable.
        try:
            import gpu_utils as _gpu_utils
        except Exception:
            return False

        status = _gpu_utils.get_gpu_status()
        if not (status.get("gpu_usable") and status.get("cupy_installed")):
            return False

        try:
            import cupy as cp
            from cupyx.scipy import ndimage as cp_ndimage
        except Exception:
            return False

        gray = self.gray  # float64 numpy

        try:
            gray_gpu = cp.asarray(gray)

            # Sobel gradients + edge magnitude — batch transfer
            gx = cp_ndimage.sobel(gray_gpu, axis=1)  # X derivative
            gy = cp_ndimage.sobel(gray_gpu, axis=0)  # Y derivative
            edges_gpu = cp.sqrt(gx * gx + gy * gy)

            # Batch transfer: stack Sobel results and download once
            sobel_stack = cp.stack([gx, gy, edges_gpu])
            sobel_np = cp.asnumpy(sobel_stack)
            self._sobel_v = sobel_np[0]
            self._sobel_h = sobel_np[1]
            self._edges = sobel_np[2]

            # Gaussian filters (shared across many families)
            #
            # Include a small superset of sigmas used across feature families
            # so GPU mode avoids CPU-side gaussian_filter calls later.
            sigma_mscn = 7.0 / 6.0  # MSSN/MSCN default sigma
            for sigma in (0.5, 1.0, sigma_mscn, 1.2, 1.5, 2.0, 3.0, 4.0, 5.0):
                out_gpu = cp_ndimage.gaussian_filter(gray_gpu, sigma=float(sigma), mode="reflect")
                out = cp.asnumpy(out_gpu)
                key = (float(sigma), "reflect")
                self._gaussian_cache[key] = out
                if sigma == 0.5:
                    self._gaussian_0_5 = out
                elif sigma == 1.0:
                    self._gaussian_1 = out
                elif sigma == 1.5:
                    self._gaussian_1p5 = out
                elif sigma == 2.0:
                    self._gaussian_2 = out
                elif sigma == 3.0:
                    self._gaussian_3 = out
                elif sigma == 5.0:
                    self._gaussian_5 = out

                # Cache Sobel of Gaussian-blurred versions used by brush multiscale anisotropy.
                if sigma in (0.5, 1.0, 2.0):
                    gx_g = cp_ndimage.sobel(out_gpu, axis=1)
                    gy_g = cp_ndimage.sobel(out_gpu, axis=0)
                    self._sobel_gaussian_cache[(float(sigma), "reflect")] = (cp.asnumpy(gx_g), cp.asnumpy(gy_g))

            # Laplacian
            lap_gpu = cp_ndimage.laplace(gray_gpu)
            self._laplacian = cp.asnumpy(lap_gpu)

            # Local mean/variance (5x5 reflect) — batch transfer
            size = 5
            mean_gpu = cp_ndimage.uniform_filter(gray_gpu, size=size, mode="reflect")
            mean_sq_gpu = cp_ndimage.uniform_filter(gray_gpu * gray_gpu, size=size, mode="reflect")
            var_gpu = cp.maximum(mean_sq_gpu - mean_gpu * mean_gpu, 0)

            local_stack = cp.stack([mean_gpu, var_gpu])
            local_np = cp.asnumpy(local_stack)
            self._uniform_cache[(size, "reflect")] = local_np[0]
            self._local_variance_cache[(size, "reflect")] = local_np[1]

            # NSS/MSCN needs gaussian(gray**2) at sigma=7/6.
            mu_sq_gpu = cp_ndimage.gaussian_filter(gray_gpu * gray_gpu, sigma=float(sigma_mscn), mode="reflect")
            self._gaussian_sq_cache[(float(sigma_mscn), "reflect")] = cp.asnumpy(mu_sq_gpu)

            # FFT bundle (shifted) — batch transfer
            fft_shift_gpu = cp.fft.fftshift(cp.fft.fft2(gray_gpu))
            mag_gpu = cp.abs(fft_shift_gpu)
            power_gpu = mag_gpu * mag_gpu
            log_mag_gpu = cp.log1p(mag_gpu)

            # Batch transfer: stack real-valued FFT results and download once
            fft_real_stack = cp.stack([mag_gpu, power_gpu, log_mag_gpu])
            fft_real_np = cp.asnumpy(fft_real_stack)
            self._fft_magnitude = fft_real_np[0]
            self._fft_power = fft_real_np[1]
            self._fft_log_magnitude = fft_real_np[2]
            self._fft_complex = cp.asnumpy(fft_shift_gpu)

            self._gpu_preloaded = True
            return True
        except Exception as e:
            # Keep this debug-level: failures should fall back silently to CPU,
            # but "GPU usable" should have filtered most systemic issues already.
            logger.debug(f"GPU preload bundle failed, using CPU caches: {e}")
            return False

    def clear_caches(self) -> None:
        """Clear all dict-based caches to free memory after feature extraction."""
        self._gaussian_cache.clear()
        self._gaussian_sq_cache.clear()
        self._uniform_cache.clear()
        self._local_variance_cache.clear()
        self._residual_cache.clear()
        self._fft_residual_cache.clear()
        self._sobel_gaussian_cache.clear()
        self._channel_residual_cache.clear()
        self._face_detections = None
        self._face_cascade_loaded = False
        self._autocorr_gray = None
        self._autocorr_texture = None
        self._eye_detections = None
        self._eye_cascade_loaded = False
        self._body_detections = None
        self._body_cascade_loaded = False
        self._wavelet_haar = None
        self._wavelet_haar_multi = None
        self._wavelet_db4 = None
        self._fft_half = None

    @property
    def face_detections(self) -> np.ndarray:
        """Cached face detection via Haar cascade. Returns array of (x,y,w,h) or empty array."""
        if not self._face_cascade_loaded:
            self._face_cascade_loaded = True
            try:
                cascade_path = cv2.data.haarcascades + 'haarcascade_frontalface_default.xml'
                face_cascade = cv2.CascadeClassifier(cascade_path)
                gray_u8 = self.gray_uint8
                self._face_detections = face_cascade.detectMultiScale(
                    gray_u8, scaleFactor=1.1, minNeighbors=4, minSize=(30, 30)
                )
            except Exception:
                self._face_detections = np.array([])
        if self._face_detections is None:
            return np.array([])
        return self._face_detections

    @property
    def autocorr_gray(self) -> np.ndarray:
        """Cached autocorrelation of grayscale via FFT (used by self-similarity).
        Reuses precomputed fft_complex to avoid a redundant forward FFT."""
        if self._autocorr_gray is None:
            from gpu_utils import gpu_ifft2
            # Reuse fft_complex = fftshift(fft2(gray))
            fft_s = self.fft_complex
            h, w = fft_s.shape
            power = fft_s.real ** 2 + fft_s.imag ** 2  # |F|^2
            power[h // 2, w // 2] = 0.0  # zero DC for zero-mean autocorrelation
            try:
                from scipy.fft import fftshift as _sp_fftshift, ifftshift as _sp_ifftshift
                self._autocorr_gray = _sp_fftshift(gpu_ifft2(_sp_ifftshift(power)).real)
            except ImportError:
                self._autocorr_gray = np.fft.fftshift(
                    np.fft.ifft2(np.fft.ifftshift(power)).real
                )
        return self._autocorr_gray

    @property
    def autocorr_texture(self) -> np.ndarray:
        """Cached autocorrelation of high-pass texture via FFT."""
        if self._autocorr_texture is None:
            texture = self.gray - self.gaussian_3
            try:
                from gpu_utils import gpu_correlate2d_fft
                self._autocorr_texture = gpu_correlate2d_fft(texture)
            except Exception:
                t = texture - np.mean(texture)
                f = np.fft.fft2(t)
                self._autocorr_texture = np.fft.fftshift(np.fft.ifft2(f * np.conj(f)).real)
        return self._autocorr_texture

    @property
    def eye_detections(self) -> np.ndarray:
        """Cached eye detection via Haar cascade. Returns array of (x,y,w,h) or empty array."""
        if not self._eye_cascade_loaded:
            self._eye_cascade_loaded = True
            try:
                cascade_path = cv2.data.haarcascades + 'haarcascade_eye.xml'
                eye_cascade = cv2.CascadeClassifier(cascade_path)
                gray_u8 = self.gray_uint8
                self._eye_detections = eye_cascade.detectMultiScale(
                    gray_u8, scaleFactor=1.1, minNeighbors=5, minSize=(20, 20)
                )
            except Exception:
                self._eye_detections = np.array([])
        if self._eye_detections is None:
            return np.array([])
        return self._eye_detections

    @property
    def body_detections(self) -> np.ndarray:
        """Cached upper body detection via Haar cascade. Returns array of (x,y,w,h) or empty array."""
        if not self._body_cascade_loaded:
            self._body_cascade_loaded = True
            try:
                cascade_path = cv2.data.haarcascades + 'haarcascade_upperbody.xml'
                body_cascade = cv2.CascadeClassifier(cascade_path)
                gray_u8 = self.gray_uint8
                self._body_detections = body_cascade.detectMultiScale(
                    gray_u8, scaleFactor=1.1, minNeighbors=3, minSize=(60, 60)
                )
            except Exception:
                self._body_detections = np.array([])
        if self._body_detections is None:
            return np.array([])
        return self._body_detections

    @property
    def wavelet_haar(self):
        """Cached single-level Haar wavelet decomposition: (cA, (cH, cV, cD))."""
        if self._wavelet_haar is None:
            try:
                import pywt
                self._wavelet_haar = pywt.dwt2(self.gray, 'haar')
            except ImportError:
                pass
        return self._wavelet_haar

    @property
    def wavelet_haar_multi(self):
        """Cached multi-level Haar wavelet decomposition (up to 3 levels)."""
        if self._wavelet_haar_multi is None:
            try:
                import pywt
                max_level = min(3, pywt.dwt_max_level(min(self.gray.shape), 'haar'))
                self._wavelet_haar_multi = pywt.wavedec2(self.gray, 'haar', level=max_level)
            except ImportError:
                pass
        return self._wavelet_haar_multi

    @property
    def wavelet_db4(self):
        """Cached multi-level db4 wavelet decomposition (up to 3 levels)."""
        if self._wavelet_db4 is None:
            try:
                import pywt
                max_level = min(3, pywt.dwt_max_level(min(self.gray.shape), 'db4'))
                self._wavelet_db4 = pywt.wavedec2(self.gray, 'db4', level=max_level)
            except ImportError:
                pass
        return self._wavelet_db4

    @property
    def fft_half(self):
        """Cached FFT of half-resolution gray (for cross-scale phase analysis)."""
        if self._fft_half is None:
            gray_s = self.gray_small
            try:
                from gpu_utils import gpu_fft2
                self._fft_half = gpu_fft2(gray_s)
            except Exception:
                self._fft_half = np.fft.fft2(gray_s)
        return self._fft_half
    
    def get_cache_sizes(self) -> dict:
        """Return size of each cache for debugging."""
        return {
            'gaussian': len(self._gaussian_cache),
            'gaussian_sq': len(self._gaussian_sq_cache),
            'uniform': len(self._uniform_cache),
            'local_variance': len(self._local_variance_cache),
            'residual': len(self._residual_cache),
            'fft_residual': len(self._fft_residual_cache),
            'sobel_gaussian': len(self._sobel_gaussian_cache),
        }
    def gaussian(self, sigma: float, mode: str = 'reflect') -> np.ndarray:
        """General-purpose Gaussian filter cache for arbitrary sigma/mode."""
        key = (float(sigma), mode)
        if key not in self._gaussian_cache:
            if _USING_GPU_UTILS and mode == 'reflect':
                self._gaussian_cache[key] = gpu_gaussian_filter(self.gray, sigma=sigma)
            elif HAS_CV2 and mode == 'reflect':
                ksize = int(6 * sigma + 1) | 1
                self._gaussian_cache[key] = cv2.GaussianBlur(self.gray, (ksize, ksize), sigma)
            else:
                self._gaussian_cache[key] = ndimage.gaussian_filter(self.gray, sigma=sigma, mode=mode)
        return self._gaussian_cache[key]

    def gaussian_sq(self, sigma: float, mode: str = 'reflect') -> np.ndarray:
        """Gaussian filter cache over gray**2 (used by NSS/MSCN)."""
        key = (float(sigma), mode)
        if key not in self._gaussian_sq_cache:
            gray_sq = self.gray * self.gray
            if _USING_GPU_UTILS and mode == 'reflect':
                self._gaussian_sq_cache[key] = gpu_gaussian_filter(gray_sq, sigma=sigma)
            elif HAS_CV2 and mode == 'reflect':
                ksize = int(6 * sigma + 1) | 1
                self._gaussian_sq_cache[key] = cv2.GaussianBlur(gray_sq, (ksize, ksize), sigma)
            else:
                self._gaussian_sq_cache[key] = ndimage.gaussian_filter(gray_sq, sigma=sigma, mode=mode)
        return self._gaussian_sq_cache[key]

    def _sobel_xy_gaussian(self, sigma: float, mode: str = 'reflect') -> Tuple[np.ndarray, np.ndarray]:
        """Compute Sobel X/Y of Gaussian-blurred grayscale, cached by (sigma, mode)."""
        key = (float(sigma), mode)
        if key not in self._sobel_gaussian_cache:
            g = self.gaussian(sigma, mode)
            if _USING_GPU_UTILS:
                sobel_v, sobel_h = gpu_sobel_xy(g)
            elif HAS_CV2:
                sobel_v = cv2.Sobel(g, cv2.CV_32F, 1, 0, ksize=3)
                sobel_h = cv2.Sobel(g, cv2.CV_32F, 0, 1, ksize=3)
            else:
                sobel_v = ndimage.sobel(g, axis=1)
                sobel_h = ndimage.sobel(g, axis=0)
            self._sobel_gaussian_cache[key] = (sobel_v, sobel_h)
        return self._sobel_gaussian_cache[key]

    def sobel_v_gaussian(self, sigma: float, mode: str = 'reflect') -> np.ndarray:
        """Vertical Sobel gradient (axis=1, X derivative) of gaussian(sigma)."""
        return self._sobel_xy_gaussian(sigma, mode)[0]

    def sobel_h_gaussian(self, sigma: float, mode: str = 'reflect') -> np.ndarray:
        """Horizontal Sobel gradient (axis=0, Y derivative) of gaussian(sigma)."""
        return self._sobel_xy_gaussian(sigma, mode)[1]

    def uniform(self, size: int, mode: str = 'reflect') -> np.ndarray:
        """General-purpose uniform filter cache for arbitrary size/mode."""
        key = (int(size), mode)
        if key not in self._uniform_cache:
            if _USING_GPU_UTILS and mode == 'reflect':
                self._uniform_cache[key] = gpu_uniform_filter(self.gray, size=size)
            elif mode == 'reflect':
                self._uniform_cache[key] = cv2.blur(self.gray, (size, size), borderType=cv2.BORDER_REFLECT)
            else:
                self._uniform_cache[key] = ndimage.uniform_filter(self.gray, size=size, mode=mode)
        return self._uniform_cache[key]

    def get_local_variance(self, size: int = 5, mode: str = 'reflect') -> np.ndarray:
        """General-purpose local variance cache for arbitrary window size/mode."""
        key = (int(size), mode)
        if key not in self._local_variance_cache:
            if _USING_GPU_UTILS and mode == 'reflect':
                self._local_variance_cache[key] = gpu_local_variance(self.gray, size=size)
            else:
                mean = self.uniform(size, mode)
                if mode == 'reflect':
                    mean_sq = cv2.blur(self.gray * self.gray, (size, size), borderType=cv2.BORDER_REFLECT)
                else:
                    mean_sq = ndimage.uniform_filter(self.gray * self.gray, size=size, mode=mode)
                self._local_variance_cache[key] = np.maximum(mean_sq - mean * mean, 0)
        return self._local_variance_cache[key]

    @property
    def img_array(self) -> np.ndarray:
        """Original image array."""
        return self._img_array

    @property
    def is_color(self) -> bool:
        """Whether the image is color (3 channels)."""
        return self._is_color

    # =========================================================================
    # GRAYSCALE
    # =========================================================================

    @property
    def gray(self) -> np.ndarray:
        """Grayscale conversion as float32 (2x faster ops, sufficient for uint8-derived data)."""
        if self._gray is None:
            if self._img_array.ndim == 3:
                self._gray = np.mean(self._img_array, axis=2).astype(np.float32)
            else:
                self._gray = self._img_array.astype(np.float32)
        return self._gray

    @property
    def gray_uint8(self) -> np.ndarray:
        """Grayscale as uint8 (for functions requiring it)."""
        if self._gray_uint8 is None:
            gray = self.gray
            if gray.max() > 1.0:
                self._gray_uint8 = gray.astype(np.uint8)
            else:
                self._gray_uint8 = (gray * 255).astype(np.uint8)
        return self._gray_uint8

    @property
    def gray_small(self) -> np.ndarray:
        """
        Downsampled grayscale for expensive per-pixel operations.
        Returns original if image is already <=512px, otherwise downsamples to ~512px.
        """
        if self._gray_small is None:
            h, w = self.gray.shape
            max_dim = max(h, w)
            if max_dim <= 512:
                self._gray_small = self.gray
                self._small_scale = 1.0
            else:
                self._small_scale = 512.0 / max_dim
                new_h, new_w = int(h * self._small_scale), int(w * self._small_scale)
                if HAS_CV2:
                    self._gray_small = cv2.resize(self.gray, (new_w, new_h), 
                                                   interpolation=cv2.INTER_AREA)
                else:
                    # Use scipy zoom for downsampling
                    from scipy.ndimage import zoom
                    self._gray_small = zoom(self.gray, self._small_scale, order=1)
        return self._gray_small

    @property
    def img_small(self) -> np.ndarray:
        """
        Downsampled color image for expensive operations.
        Returns original if image is already <=512px, otherwise downsamples to ~512px.
        """
        if self._img_small is None:
            h, w = self._img_array.shape[:2]
            max_dim = max(h, w)
            if max_dim <= 512:
                self._img_small = self._img_array
                self._small_scale = 1.0
            else:
                self._small_scale = 512.0 / max_dim
                new_h, new_w = int(h * self._small_scale), int(w * self._small_scale)
                if HAS_CV2:
                    self._img_small = cv2.resize(self._img_array, (new_w, new_h), 
                                                  interpolation=cv2.INTER_AREA)
                else:
                    from scipy.ndimage import zoom
                    if self._img_array.ndim == 3:
                        self._img_small = zoom(self._img_array, (self._small_scale, self._small_scale, 1), order=1)
                    else:
                        self._img_small = zoom(self._img_array, self._small_scale, order=1)
        return self._img_small

    @property
    def small_scale(self) -> float:
        """Scale factor from original to small version (1.0 if no downsampling)."""
        if self._gray_small is None:
            _ = self.gray_small  # Trigger computation
        return self._small_scale

    # =========================================================================
    # GRADIENTS
    # =========================================================================

    @property
    def sobel_h(self) -> np.ndarray:
        """Horizontal Sobel gradient (axis=0, Y derivative). float32."""
        if self._sobel_h is None:
            if _USING_GPU_UTILS:
                gx, gy = gpu_sobel_xy(self.gray)
                self._sobel_v = gx  # axis=1
                self._sobel_h = gy  # axis=0
            elif HAS_CV2:
                self._sobel_h = cv2.Sobel(self.gray, cv2.CV_32F, 0, 1, ksize=3)
            else:
                self._sobel_h = ndimage.sobel(self.gray, axis=0).astype(np.float32)
        return self._sobel_h

    @property
    def sobel_v(self) -> np.ndarray:
        """Vertical Sobel gradient (axis=1, X derivative). float32."""
        if self._sobel_v is None:
            if _USING_GPU_UTILS:
                gx, gy = gpu_sobel_xy(self.gray)
                self._sobel_v = gx  # axis=1
                self._sobel_h = gy  # axis=0
            elif HAS_CV2:
                self._sobel_v = cv2.Sobel(self.gray, cv2.CV_32F, 1, 0, ksize=3)
            else:
                self._sobel_v = ndimage.sobel(self.gray, axis=1).astype(np.float32)
        return self._sobel_v

    @property
    def edges(self) -> np.ndarray:
        """Sobel edge magnitude."""
        if self._edges is None:
            self._edges = np.sqrt(self.sobel_h**2 + self.sobel_v**2)
        return self._edges

    @property
    def edge_binary(self) -> np.ndarray:
        """Binary edge map (thresholded at 90th percentile)."""
        if self._edge_binary is None:
            edges = self.edges
            flat = edges.ravel()
            idx = int(0.90 * (len(flat) - 1))
            threshold = np.partition(flat, idx)[idx]
            self._edge_binary = edges > threshold
        return self._edge_binary
    
    @property
    def gradient_direction(self) -> np.ndarray:
        """Gradient direction (arctan2 of sobel gradients)."""
        if self._gradient_direction is None:
            self._gradient_direction = np.arctan2(self.sobel_v, self.sobel_h)
        return self._gradient_direction

    # =========================================================================
    # FFT
    # =========================================================================

    @property
    def fft_complex(self) -> np.ndarray:
        """Full complex FFT (shifted to center). Uses scipy.fft when available for better performance."""
        if self._fft_complex is None:
            self._fft_complex = _fft2_shift(self.gray)
        return self._fft_complex

    @property
    def fft_magnitude(self) -> np.ndarray:
        """FFT magnitude."""
        if self._fft_magnitude is None:
            self._fft_magnitude = np.abs(self.fft_complex)
        return self._fft_magnitude

    @property
    def fft_power(self) -> np.ndarray:
        """FFT power spectrum (magnitude^2)."""
        if self._fft_power is None:
            self._fft_power = self.fft_magnitude ** 2
        return self._fft_power

    @property
    def fft_log_magnitude(self) -> np.ndarray:
        """Log of FFT magnitude."""
        if self._fft_log_magnitude is None:
            self._fft_log_magnitude = np.log1p(self.fft_magnitude)
        return self._fft_log_magnitude

    @property
    def fft_phase(self) -> np.ndarray:
        """Phase of unshifted FFT (cached, reused by phase features).
        Since angle is element-wise, angle(ifftshift(F)) = ifftshift(angle(F)),
        so we compute angle of the shifted FFT and ifftshift as needed."""
        if self._fft_phase is None:
            self._fft_phase = np.angle(np.fft.ifftshift(self.fft_complex))
        return self._fft_phase

    # =========================================================================
    # LOCAL STATISTICS
    # =========================================================================

    @property
    def local_mean(self) -> np.ndarray:
        """Local mean using 5x5 uniform filter."""
        return self.uniform(size=5, mode='reflect')

    @property
    def local_variance(self) -> np.ndarray:
        """Local variance using 5x5 uniform filter."""
        return self.get_local_variance(size=5, mode='reflect')

    # =========================================================================
    # FILTERED VERSIONS
    # =========================================================================

    @property
    def gaussian_0_5(self) -> np.ndarray:
        """Gaussian filtered (sigma=0.5)."""
        if self._gaussian_0_5 is None:
            if _USING_GPU_UTILS:
                self._gaussian_0_5 = gpu_gaussian_filter(self.gray, sigma=0.5)
            elif HAS_CV2:
                self._gaussian_0_5 = cv2.GaussianBlur(self.gray, (5, 5), 0.5)
            else:
                self._gaussian_0_5 = ndimage.gaussian_filter(self.gray, sigma=0.5)
        return self._gaussian_0_5

    @property
    def gaussian_1(self) -> np.ndarray:
        """Gaussian filtered (sigma=1)."""
        if self._gaussian_1 is None:
            if _USING_GPU_UTILS:
                self._gaussian_1 = gpu_gaussian_filter(self.gray, sigma=1)
            elif HAS_CV2:
                self._gaussian_1 = cv2.GaussianBlur(self.gray, (7, 7), 1)
            else:
                self._gaussian_1 = ndimage.gaussian_filter(self.gray, sigma=1)
        return self._gaussian_1

    @property
    def gaussian_1p5(self) -> np.ndarray:
        """Gaussian filtered (sigma=1.5)."""
        if self._gaussian_1p5 is None:
            if _USING_GPU_UTILS:
                self._gaussian_1p5 = gpu_gaussian_filter(self.gray, sigma=1.5)
            elif HAS_CV2:
                self._gaussian_1p5 = cv2.GaussianBlur(self.gray, (11, 11), 1.5)
            else:
                self._gaussian_1p5 = ndimage.gaussian_filter(self.gray, sigma=1.5)
        return self._gaussian_1p5

    @property
    def gaussian_2(self) -> np.ndarray:
        """Gaussian filtered (sigma=2)."""
        if self._gaussian_2 is None:
            if _USING_GPU_UTILS:
                self._gaussian_2 = gpu_gaussian_filter(self.gray, sigma=2)
            elif HAS_CV2:
                self._gaussian_2 = cv2.GaussianBlur(self.gray, (13, 13), 2)
            else:
                self._gaussian_2 = ndimage.gaussian_filter(self.gray, sigma=2)
        return self._gaussian_2

    @property
    def gaussian_3(self) -> np.ndarray:
        """Gaussian filtered (sigma=3)."""
        if self._gaussian_3 is None:
            if _USING_GPU_UTILS:
                self._gaussian_3 = gpu_gaussian_filter(self.gray, sigma=3)
            elif HAS_CV2:
                self._gaussian_3 = cv2.GaussianBlur(self.gray, (19, 19), 3)
            else:
                self._gaussian_3 = ndimage.gaussian_filter(self.gray, sigma=3)
        return self._gaussian_3

    @property
    def gaussian_5(self) -> np.ndarray:
        """Gaussian filtered (sigma=5)."""
        if self._gaussian_5 is None:
            if _USING_GPU_UTILS:
                self._gaussian_5 = gpu_gaussian_filter(self.gray, sigma=5)
            elif HAS_CV2:
                self._gaussian_5 = cv2.GaussianBlur(self.gray, (31, 31), 5)
            else:
                self._gaussian_5 = ndimage.gaussian_filter(self.gray, sigma=5)
        return self._gaussian_5

    @property
    def laplacian(self) -> np.ndarray:
        """Laplacian of grayscale image."""
        if self._laplacian is None:
            if _USING_GPU_UTILS:
                self._laplacian = gpu_laplacian(self.gray)
            elif HAS_CV2:
                self._laplacian = cv2.Laplacian(
                    self.gray, cv2.CV_32F, ksize=3
                )
            else:
                self._laplacian = ndimage.laplace(self.gray)
        return self._laplacian

    @property
    def high_pass(self) -> np.ndarray:
        """High-pass filtered (gray - gaussian_1). Useful for texture/checkerboard detection."""
        if self._high_pass is None:
            self._high_pass = self.gray - self.gaussian_1
        return self._high_pass    
    @property
    def residual_1p5(self) -> np.ndarray:
        """Residual: gray - gaussian_1p5 (commonly used in CFA/residual features)."""
        if self._residual_1p5 is None:
            self._residual_1p5 = self.gray - self.gaussian_1p5
        return self._residual_1p5
    
    @property
    def residual_2(self) -> np.ndarray:
        """Residual: gray - gaussian_2."""
        if self._residual_2 is None:
            self._residual_2 = self.gray - self.gaussian_2
        return self._residual_2
    
    def get_residual(self, sigma: float) -> np.ndarray:
        """Get residual (gray - gaussian) for arbitrary sigma, with caching."""
        key = float(sigma)
        if key not in self._residual_cache:
            self._residual_cache[key] = self.gray - self.gaussian(sigma)
        return self._residual_cache[key]
    
    def get_fft_residual(self, sigma: float) -> np.ndarray:
        """Get FFT of residual for arbitrary sigma, with caching. Uses GPU when available."""
        key = float(sigma)
        if key not in self._fft_residual_cache:
            residual = self.get_residual(sigma)
            self._fft_residual_cache[key] = _fft2(residual)
        return self._fft_residual_cache[key]

    def get_channel_residual(self, channel_idx: int, sigma: float) -> np.ndarray:
        """Get per-channel Gaussian residual (channel - blur), with caching.
        
        Eliminates redundant per-channel denoising across noise/forensic modules.
        
        Args:
            channel_idx: Color channel index (0=R, 1=G, 2=B)
            sigma: Gaussian blur sigma for denoising
            
        Returns:
            Residual array (H, W) as float32
        """
        key = (int(channel_idx), float(sigma))
        if key not in self._channel_residual_cache:
            ch = self._img_array[:, :, channel_idx].astype(np.float32)
            if HAS_CV2:
                denoised = cv2.GaussianBlur(ch, (0, 0), sigma)
            elif _USING_GPU_UTILS:
                denoised = gpu_gaussian_filter(ch, sigma=sigma)
            else:
                denoised = ndimage.gaussian_filter(ch, sigma=sigma)
            self._channel_residual_cache[key] = ch - denoised
        return self._channel_residual_cache[key]

    # =========================================================================
    # COLOR CHANNELS
    # =========================================================================

    @property
    def r(self) -> Optional[np.ndarray]:
        """Red channel as float32."""
        if self._r is None and self._is_color:
            self._r = self._img_array[:, :, 0].astype(np.float32)
        return self._r

    @property
    def g(self) -> Optional[np.ndarray]:
        """Green channel as float32."""
        if self._g is None and self._is_color:
            self._g = self._img_array[:, :, 1].astype(np.float32)
        return self._g

    @property
    def b(self) -> Optional[np.ndarray]:
        """Blue channel as float32."""
        if self._b is None and self._is_color:
            self._b = self._img_array[:, :, 2].astype(np.float32)
        return self._b

    # =========================================================================
    # COLOR SPACES
    # =========================================================================

    @property
    def hsv(self) -> Optional[np.ndarray]:
        """HSV color space conversion."""
        if self._hsv is None and self._is_color:
            if HAS_CV2:
                self._hsv = cv2.cvtColor(self._img_array, cv2.COLOR_RGB2HSV).astype(np.float32)
            else:
                from PIL import Image
                img = Image.fromarray(self._img_array.astype(np.uint8))
                self._hsv = np.array(img.convert('HSV'), dtype=np.float32)
        return self._hsv

    @property
    def ycbcr(self) -> Optional[np.ndarray]:
        """YCbCr color space conversion (Y, Cb, Cr order)."""
        if self._ycbcr is None and self._is_color:
            if HAS_CV2:
                ycrcb = cv2.cvtColor(self._img_array, cv2.COLOR_RGB2YCrCb).astype(np.float32)
                # Reorder to Y, Cb, Cr
                self._ycbcr = np.stack([ycrcb[:, :, 0], ycrcb[:, :, 2], ycrcb[:, :, 1]], axis=2)
            else:
                from PIL import Image
                img = Image.fromarray(self._img_array.astype(np.uint8))
                self._ycbcr = np.array(img.convert('YCbCr'), dtype=np.float32)
        return self._ycbcr

    @property
    def lab(self) -> Optional[np.ndarray]:
        """LAB color space conversion."""
        if self._lab is None and self._is_color:
            if HAS_CV2:
                lab_cv = cv2.cvtColor(self._img_array, cv2.COLOR_RGB2LAB).astype(np.float32)
                # Convert to standard LAB ranges
                l = lab_cv[:, :, 0] * (100.0 / 255.0)
                a = lab_cv[:, :, 1] - 128.0
                b = lab_cv[:, :, 2] - 128.0
                self._lab = np.stack([l, a, b], axis=2)
            else:
                try:
                    from skimage import color
                    self._lab = color.rgb2lab(self._img_array / 255.0)
                except ImportError:
                    self._lab = None
        return self._lab

    # =========================================================================
    # NOISE
    # =========================================================================

    @property
    def noise_residual(self) -> np.ndarray:
        """Estimated noise residual (gray - gaussian_1p5)."""
        if self._noise_residual is None:
            self._noise_residual = self.gray - self.gaussian_1p5
        return self._noise_residual

    @property
    def gradient_direction(self) -> np.ndarray:
        """Gradient direction in radians."""
        if self._gradient_direction is None:
            self._gradient_direction = np.arctan2(self.sobel_h, self.sobel_v)
        return self._gradient_direction

    @property
    def local_std(self) -> np.ndarray:
        """Local standard deviation (sqrt of local_variance)."""
        if self._local_std is None:
            self._local_std = np.sqrt(self.local_variance)
        return self._local_std

    @property
    def gray_histogram(self) -> np.ndarray:
        """Grayscale histogram (256 bins, normalized)."""
        if self._gray_histogram is None:
            if HAS_CV2:
                hist = cv2.calcHist([self.gray_uint8], [0], None, [256], [0, 256]).ravel()
            else:
                hist = np.bincount(self.gray_uint8.ravel(), minlength=256)[:256].astype(np.float64)
            self._gray_histogram = hist.astype(np.float64) / (hist.sum() + 1e-10)
        return self._gray_histogram

    @property
    def gray_histogram_raw(self) -> np.ndarray:
        """Raw grayscale histogram counts (256 bins) — for fast percentile lookups."""
        if self._gray_histogram_raw is None:
            self._gray_histogram_raw = np.bincount(self.gray_uint8.ravel(), minlength=256)[:256]
        return self._gray_histogram_raw

    @property
    def gray_histogram_cumsum(self) -> np.ndarray:
        """Cumulative sum of raw gray histogram — for O(1) percentile lookups."""
        if self._gray_histogram_cumsum is None:
            self._gray_histogram_cumsum = np.cumsum(self.gray_histogram_raw)
        return self._gray_histogram_cumsum

    @property
    def gray_mean(self) -> float:
        """Global mean of gray image (cached)."""
        if self._gray_mean is None:
            self._gray_mean = float(self.gray.mean())
        return self._gray_mean

    @property
    def gray_var(self) -> float:
        """Global variance of gray image (cached)."""
        if self._gray_var is None:
            m = self.gray_mean
            self._gray_var = float(np.mean((self.gray - m) ** 2))
        return self._gray_var

    @property
    def gray_std(self) -> float:
        """Global std dev of gray image (cached)."""
        if self._gray_std is None:
            import math
            self._gray_std = math.sqrt(self.gray_var)
        return self._gray_std

    @staticmethod
    def mean_std(arr: np.ndarray):
        """Compute mean and std of an array in one pass (avoids double reduce)."""
        m = arr.mean()
        s = np.sqrt(np.mean((arr - m) ** 2))
        return float(m), float(s)

    def gray_percentile(self, percentile: float) -> float:
        """Fast O(1) percentile of gray values using precomputed histogram.
        
        Args:
            percentile: Value in [0, 100].
        Returns:
            The approximate percentile value (integer from 0-255).
        """
        cumsum = self.gray_histogram_cumsum
        n = cumsum[-1]  # total pixel count
        target = n * (percentile / 100.0)
        return float(np.searchsorted(cumsum, target))

    def gray_percentiles(self, percentiles: list) -> list:
        """Fast batch percentile lookup of gray values."""
        cumsum = self.gray_histogram_cumsum
        n = cumsum[-1]
        return [float(np.searchsorted(cumsum, n * (p / 100.0))) for p in percentiles]

    def channel_histogram(self, channel_name: str) -> np.ndarray:
        """256-bin normalised histogram for 'r', 'g', or 'b' channel (from uint8)."""
        if self._channel_histograms is None:
            self._channel_histograms = {}
        if channel_name not in self._channel_histograms:
            idx = {'r': 0, 'g': 1, 'b': 2}[channel_name]
            ch = self._img_array[:, :, idx]  # uint8
            if HAS_CV2:
                hist = cv2.calcHist([ch], [0], None, [256], [0, 256]).ravel()
            else:
                hist = np.bincount(ch.ravel(), minlength=256)[:256].astype(np.float64)
            self._channel_histograms[channel_name] = hist.astype(np.float64) / (hist.sum() + 1e-10)
        return self._channel_histograms[channel_name]

    @property
    def boundary_diffs(self):
        """Cached (row_diffs, col_diffs) from blockgrid.line_boundary_diffs."""
        if self._line_boundary_diffs is None:
            from blockgrid import line_boundary_diffs
            self._line_boundary_diffs = line_boundary_diffs(self.gray)
        return self._line_boundary_diffs

    @property
    def edge_dilated(self) -> np.ndarray:
        """Dilated binary edge map (for near-edge region analysis)."""
        if self._edge_dilated is None:
            if _USING_GPU_UTILS:
                self._edge_dilated = gpu_binary_dilation(self.edge_binary, iterations=5)
            else:
                self._edge_dilated = ndimage.binary_dilation(self.edge_binary, iterations=5)
        return self._edge_dilated

    # =========================================================================
    # CONVENIENCE METHODS
    # =========================================================================

    def get_channel_sobel(self, channel_idx: int) -> Tuple[np.ndarray, np.ndarray]:
        """Get Sobel gradients for a specific color channel."""
        if not self._is_color or channel_idx >= 3:
            return self.sobel_h, self.sobel_v

        channel = self._img_array[:, :, channel_idx].astype(np.float32)
        if _USING_GPU_UTILS:
            gx, gy = gpu_sobel_xy(channel)
            return gy, gx  # sobel_h=axis=0, sobel_v=axis=1
        elif HAS_CV2:
            sobel_h = cv2.Sobel(channel, cv2.CV_32F, 0, 1, ksize=3)
            sobel_v = cv2.Sobel(channel, cv2.CV_32F, 1, 0, ksize=3)
        else:
            sobel_h = ndimage.sobel(channel, axis=0)
            sobel_v = ndimage.sobel(channel, axis=1)
        return sobel_h, sobel_v

    def get_channel_edges(self, channel_idx: int) -> np.ndarray:
        """Get edge magnitude for a specific color channel."""
        sobel_h, sobel_v = self.get_channel_sobel(channel_idx)
        return np.sqrt(sobel_h**2 + sobel_v**2)

    # =========================================================================
    # HOUGH LINES (cached, used by physics perspective + horizon)
    # =========================================================================

    @property
    def canny_edges(self) -> np.ndarray:
        """Canny edge map (threshold 50, 150) for line detection."""
        if self._canny_edges_50_150 is None:
            self._canny_edges_50_150 = cv2.Canny(self.gray_uint8, 50, 150)
        return self._canny_edges_50_150

    @property
    def hough_lines_sensitive(self):
        """HoughLinesP with sensitive params (threshold=50, minLineLength=30, maxLineGap=10). Cached."""
        if self._hough_lines_sensitive is None:
            self._hough_lines_sensitive = cv2.HoughLinesP(
                self.canny_edges, 1, np.pi / 180,
                threshold=50, minLineLength=30, maxLineGap=10
            )
        return self._hough_lines_sensitive
