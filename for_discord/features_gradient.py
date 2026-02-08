"""
features_gradient.py - Gradient & Artifact Analysis for AI detection.

Implements Differential Convolutions (DCs), pixel-level inconsistency analysis,
and enhanced artifact feature extraction to detect AI-generated images.

Key techniques:
- Differential Convolutions to extract gradient representations from artifact locations
- Pixel-level inconsistency and compression artifact analysis
- High-frequency artifact detection common in diffusion and GAN models
"""

import numpy as np
from scipy import ndimage, signal, stats
from typing import Dict, List, Tuple, Optional, TYPE_CHECKING
import warnings

# GPU acceleration utilities
from gpu_utils import (
    gpu_convolve,
    gpu_sobel,
    gpu_uniform_filter,
    gpu_fft2,
    gpu_fftshift,
    gpu_gaussian_filter,
    gpu_fft_magnitude_shifted,
)

if TYPE_CHECKING:
    from image_precomputed import ImagePrecomputedData

# Optional OpenCV acceleration
try:
    import cv2
    HAS_CV2 = True
except ImportError:
    HAS_CV2 = False

# Try JIT-compiled correlation (fastest after warmup)
try:
    from jit_utils import fast_corrcoef, fast_entropy, fast_local_std_2d
    HAS_JIT_CORRCOEF = True
except ImportError:
    HAS_JIT_CORRCOEF = False

# Fast FFT using scipy.fft with multi-threading (3-4x faster than numpy.fft)
try:
    from scipy.fft import fft2 as _sp_fft2, ifft2 as _sp_ifft2, fftshift as _sp_fftshift, ifftshift as _sp_ifftshift, fft as _sp_fft
    def _fast_fft2(x, **kw): return _sp_fft2(x, workers=-1, **kw)
    def _fast_ifft2(x, **kw): return _sp_ifft2(x, workers=-1, **kw)
    def _fast_fft(x, **kw): return _sp_fft(x, workers=-1, **kw)
    _fast_fftshift = _sp_fftshift
    _fast_ifftshift = _sp_ifftshift
except ImportError:
    from numpy.fft import fft2 as _fast_fft2, ifft2 as _fast_ifft2, fftshift as _fast_fftshift, ifftshift as _fast_ifftshift, fft as _fast_fft


def _entropy(hist):
    """Fast histogram entropy in bits."""
    if HAS_JIT_CORRCOEF:
        return float(fast_entropy(hist))
    return float(-np.sum(hist * np.log2(hist + 1e-10)))


def _fast_corrcoef(a: np.ndarray, b: np.ndarray) -> float:
    """Fast correlation between two 1D arrays."""
    a = np.asarray(a, dtype=np.float64).ravel()
    b = np.asarray(b, dtype=np.float64).ravel()
    if a.size < 2 or b.size < 2:
        return 0.0
    if HAS_JIT_CORRCOEF:
        return float(fast_corrcoef(a, b))
    a_mean = np.mean(a)
    b_mean = np.mean(b)
    a_centered = a - a_mean
    b_centered = b - b_mean
    denom = np.sqrt(np.dot(a_centered, a_centered) * np.dot(b_centered, b_centered)) + 1e-10
    return float(np.dot(a_centered, b_centered) / denom)


def compute_differential_convolutions(
    img_array: np.ndarray,
    *,
    gray: Optional[np.ndarray] = None,
    precomputed: Optional["ImagePrecomputedData"] = None,
) -> Dict[str, float]:
    """
    Extract gradient representations using Differential Convolutions (DCs).
    
    DCs capture local gradient patterns that often reveal AI artifacts.
    Applies multiple directional gradient kernels to detect anomalies.
    """
    features = {}
    
    gray = precomputed.gray
    
    # Define differential convolution kernels for artifact detection
    # Horizontal gradient
    kernel_h = np.array([[-1, 0, 1],
                         [-2, 0, 2],
                         [-1, 0, 1]], dtype=np.float64)
    
    # Vertical gradient
    kernel_v = np.array([[-1, -2, -1],
                         [0, 0, 0],
                         [1, 2, 1]], dtype=np.float64)
    
    # Diagonal gradients
    kernel_d1 = np.array([[0, 1, 2],
                          [-1, 0, 1],
                          [-2, -1, 0]], dtype=np.float64)
    
    kernel_d2 = np.array([[-2, -1, 0],
                          [-1, 0, 1],
                          [0, 1, 2]], dtype=np.float64)
    
    # Second-order derivatives (Laplacian variants)
    kernel_lap = np.array([[0, 1, 0],
                           [1, -4, 1],
                           [0, 1, 0]], dtype=np.float64)
    
    kernel_lap_diag = np.array([[1, 0, 1],
                                [0, -4, 0],
                                [1, 0, 1]], dtype=np.float64)
    
    # 3rd order gradient (detects subtle transitions)
    kernel_3rd = np.array([[-1, 2, -1],
                           [2, -4, 2],
                           [-1, 2, -1]], dtype=np.float64)
    
    kernels = {
        'dc_h': kernel_h,
        'dc_v': kernel_v,
        'dc_d1': kernel_d1,
        'dc_d2': kernel_d2,
        'dc_lap': kernel_lap,
        'dc_lap_diag': kernel_lap_diag,
        'dc_3rd': kernel_3rd,
    }
    
    gradient_maps = {}
    if HAS_CV2:
        gray32 = gray.astype(np.float32)
        grad_h = precomputed.sobel_v  # X derivative (was cv2.Sobel dx=1)
        grad_v = precomputed.sobel_h  # Y derivative (was cv2.Sobel dy=1)
        gradient_maps['dc_h'] = grad_h
        gradient_maps['dc_v'] = grad_v
        for name, kernel in kernels.items():
            if name in ('dc_h', 'dc_v'):
                continue
            grad = cv2.filter2D(gray32, cv2.CV_32F, kernel)
            gradient_maps[name] = grad
    else:
        for name, kernel in kernels.items():
            grad = gpu_convolve(gray, kernel, mode='reflect')
            gradient_maps[name] = grad

    # Statistics from each gradient map
    for name, grad in gradient_maps.items():
        abs_grad = np.abs(grad)

        features[f'{name}_mean'] = float(np.mean(abs_grad))
        features[f'{name}_std'] = float(np.std(grad))
        features[f'{name}_max'] = float(np.max(abs_grad))
        features[f'{name}_energy'] = float(np.sum(grad ** 2) / grad.size)

        _flat_ag = abs_grad.ravel()
        _idx90 = int(0.90 * (len(_flat_ag) - 1))
        threshold = np.partition(_flat_ag, _idx90)[_idx90]
        top_10_energy = np.sum(abs_grad[abs_grad >= threshold])
        total = np.sum(abs_grad) + 1e-10
        features[f'{name}_sparsity'] = float(top_10_energy / total)
    
    # Cross-gradient analysis (detect inconsistent gradient directions)
    grad_h = gradient_maps['dc_h']
    grad_v = gradient_maps['dc_v']
    
    # Gradient magnitude and direction
    magnitude = np.sqrt(grad_h ** 2 + grad_v ** 2)
    direction = np.arctan2(grad_v, grad_h)
    
    features['dc_magnitude_mean'] = float(np.mean(magnitude))
    features['dc_magnitude_std'] = float(np.std(magnitude))
    
    # Direction histogram entropy (AI often has less varied gradient directions)
    dir_hist, _ = np.histogram(direction.ravel(), bins=36, range=(-np.pi, np.pi))
    dir_hist = dir_hist / (dir_hist.sum() + 1e-10)
    features['dc_direction_entropy'] = _entropy(dir_hist)
    
    # Gradient coherence (how consistent are gradient directions locally)
    coherence = compute_gradient_coherence(direction, magnitude)
    features['dc_gradient_coherence'] = float(coherence)
    
    return features


def compute_gradient_coherence(direction: np.ndarray, magnitude: np.ndarray, 
                                window_size: int = 7) -> float:
    """
    Compute local gradient coherence - how consistent gradient directions are.
    
    AI images often have either too uniform or inconsistent gradient patterns.
    Uses cv2.blur for fast sliding window computation (4x faster than scipy).
    """
    h, w = direction.shape
    
    # Compute sin and cos weighted by magnitude
    sin_weighted = (magnitude * np.sin(direction)).astype(np.float32)
    cos_weighted = (magnitude * np.cos(direction)).astype(np.float32)
    mag_f32 = magnitude.astype(np.float32)
    
    # Use cv2.blur for fast sliding window sums (4x faster)
    if HAS_CV2:
        # cv2.blur returns average, multiply by window^2 to get sum
        sin_sum = cv2.blur(sin_weighted, (window_size, window_size)) * (window_size ** 2)
        cos_sum = cv2.blur(cos_weighted, (window_size, window_size)) * (window_size ** 2)
        mag_sum = cv2.blur(mag_f32, (window_size, window_size)) * (window_size ** 2)
    else:
        sin_sum = gpu_uniform_filter(sin_weighted, size=window_size) * (window_size ** 2)
        cos_sum = gpu_uniform_filter(cos_weighted, size=window_size) * (window_size ** 2)
        mag_sum = gpu_uniform_filter(magnitude, size=window_size) * (window_size ** 2)
    
    # Normalize by magnitude sum
    sin_norm = sin_sum / (mag_sum + 1e-10)
    cos_norm = cos_sum / (mag_sum + 1e-10)
    
    # Coherence: how aligned the directions are - use np.hypot (faster)
    coherence_map = np.hypot(sin_norm, cos_norm)
    
    return float(np.mean(coherence_map))


def compute_pixel_inconsistency_features(
    img_array: np.ndarray,
    *,
    gray: Optional[np.ndarray] = None,
    precomputed: Optional["ImagePrecomputedData"] = None,
) -> Dict[str, float]:
    """
    Detect pixel-level inconsistencies and anomalies.
    
    AI-generated images often have subtle pixel-level irregularities:
    - Unusual local variance patterns
    - Inconsistent pixel value distributions
    - Anomalous color transitions
    """
    features = {}
    
    # Per-channel analysis
    for c, channel_name in enumerate(['r', 'g', 'b']):
        channel = img_array[:, :, c].astype(np.float32)  # float32 for cv2
        
        # Local variance using cv2.blur (4x faster than scipy uniform_filter)
        if HAS_CV2:
            mean = cv2.blur(channel, (5, 5))
            mean_sq = cv2.blur(channel * channel, (5, 5))
            local_var = np.maximum(mean_sq - mean * mean, 0)
        elif HAS_JIT_CORRCOEF:
            local_std = fast_local_std_2d(channel.astype(np.float32), 5)
            local_var = local_std * local_std
        else:
            mean = gpu_uniform_filter(channel.astype(np.float32), size=5)
            mean_sq = gpu_uniform_filter((channel * channel).astype(np.float32), size=5)
            local_var = np.maximum(mean_sq - mean * mean, 0)
        
        # Statistics of local variance
        features[f'pix_{channel_name}_local_var_mean'] = float(np.mean(local_var))
        features[f'pix_{channel_name}_local_var_std'] = float(np.std(local_var))
        
        # Detect anomalous variance regions - use partition for O(n) percentiles
        local_var_flat = local_var.ravel()
        n = len(local_var_flat)
        p5_idx = max(0, int(0.05 * n) - 1)
        p95_idx = min(n - 1, int(0.95 * n))
        partitioned = np.partition(local_var_flat, [p5_idx, p95_idx])
        var_threshold_low = partitioned[p5_idx]
        var_threshold_high = partitioned[p95_idx]
        
        features[f'pix_{channel_name}_low_var_ratio'] = float(np.mean(local_var < var_threshold_low))
        features[f'pix_{channel_name}_high_var_ratio'] = float(np.mean(local_var > var_threshold_high))
    
    # Cross-channel pixel inconsistency - use ravel (view) instead of flatten (copy)
    r = precomputed.r.ravel()
    g = precomputed.g.ravel()
    b = precomputed.b.ravel()
    
    features['pix_corr_rg'] = float(_fast_corrcoef(r, g)) if len(r) > 1 else 0.0
    features['pix_corr_rb'] = float(_fast_corrcoef(r, b)) if len(r) > 1 else 0.0
    features['pix_corr_gb'] = float(_fast_corrcoef(g, b)) if len(g) > 1 else 0.0
    
    # Detect flat regions (exactly same pixel values - suspicious for AI)
    gray = precomputed.gray
    
    # Count regions of identical pixel values
    # Use integer binning for speed - convert to uint8 range for efficiency
    gray_int = (gray * 255 / (gray.max() + 1e-10)).astype(np.uint8)
    unique_vals, counts = np.unique(gray_int, return_counts=True)
    features['pix_unique_ratio'] = float(len(unique_vals) / gray.size)
    
    # Largest flat region
    features['pix_max_flat_size'] = float(np.max(counts) / gray.size)
    
    # Histogram analysis - AI often has unusual value distributions
    for c, channel_name in enumerate(['r', 'g', 'b']):
        channel = img_array[:, :, c]
        # Use bincount for speed
        channel_int = (channel * 255).astype(np.int32).ravel()
        channel_int = np.clip(channel_int, 0, 255)
        hist = np.bincount(channel_int, minlength=256).astype(np.float64)
        hist = hist / (hist.sum() + 1e-10)
        
        # Histogram entropy
        features[f'pix_{channel_name}_hist_entropy'] = _entropy(hist)
        
        # Count zero bins (AI may have gaps in histogram)
        features[f'pix_{channel_name}_zero_bins'] = float(np.sum(hist == 0))
        
        # Histogram smoothness
        hist_diff = np.abs(np.diff(hist))
        features[f'pix_{channel_name}_hist_smoothness'] = float(np.mean(hist_diff))
    
    return features


def compute_artifact_location_features(
    img_array: np.ndarray,
    *,
    gray: Optional[np.ndarray] = None,
    edge_mag: Optional[np.ndarray] = None,
    precomputed: Optional["ImagePrecomputedData"] = None,
) -> Dict[str, float]:
    """
    Detect artifacts at typical AI-problematic locations.
    
    AI often has artifacts in:
    - Image borders/edges
    - Fine detail areas
    - High contrast regions
    """
    features = {}
    gray = precomputed.gray
    h, w = gray.shape
    
    # Border analysis (AI often has edge artifacts)
    border_width = min(10, h // 20, w // 20)
    if border_width > 0:
        # Extract borders
        top_border = gray[:border_width, :]
        bottom_border = gray[-border_width:, :]
        left_border = gray[:, :border_width]
        right_border = gray[:, -border_width:]
        
        interior = gray[border_width:-border_width, border_width:-border_width]
        
        # Compare border vs interior statistics
        border_all = np.concatenate([top_border.ravel(), bottom_border.ravel(),
                                      left_border.ravel(), right_border.ravel()])
        
        features['artifact_border_mean_diff'] = float(abs(np.mean(border_all) - np.mean(interior)))
        features['artifact_border_std_diff'] = float(abs(np.std(border_all) - np.std(interior)))
        
        # Border gradient analysis
        border_gradient = np.abs(np.diff(border_all[:100])) if len(border_all) > 100 else np.array([0])
        interior_sample = interior.ravel()[:1000]
        interior_gradient = np.abs(np.diff(interior_sample)) if len(interior_sample) > 1 else np.array([0])
        
        features['artifact_border_gradient_mean'] = float(np.mean(border_gradient))
        features['artifact_interior_gradient_mean'] = float(np.mean(interior_gradient))
    else:
        features['artifact_border_mean_diff'] = 0.0
        features['artifact_border_std_diff'] = 0.0
        features['artifact_border_gradient_mean'] = 0.0
        features['artifact_interior_gradient_mean'] = 0.0
    
    # High contrast region analysis
    if edge_mag is None:
        edge_mag = precomputed.edges
    _flat_em = edge_mag.ravel()
    _idx80 = int(0.80 * (len(_flat_em) - 1))
    high_contrast_mask = edge_mag > np.partition(_flat_em, _idx80)[_idx80]
    
    # Analyze noise in high-contrast regions
    denoised = precomputed.gaussian_1
    noise = gray - denoised
    
    hc_noise = noise[high_contrast_mask]
    lc_noise = noise[~high_contrast_mask]
    
    features['artifact_hc_noise_std'] = float(np.std(hc_noise)) if len(hc_noise) > 0 else 0.0
    features['artifact_lc_noise_std'] = float(np.std(lc_noise)) if len(lc_noise) > 0 else 0.0
    features['artifact_noise_ratio'] = float(features['artifact_hc_noise_std'] / 
                                              (features['artifact_lc_noise_std'] + 1e-10))
    
    return features


def compute_upsampling_artifact_features(
    img_array: np.ndarray,
    *,
    gray: Optional[np.ndarray] = None,
    precomputed: Optional["ImagePrecomputedData"] = None,
) -> Dict[str, float]:
    """
    Detect upsampling/super-resolution artifacts.
    
    Many AI images are generated at lower resolution and upsampled,
    leaving characteristic artifacts.
    """
    features = {}
    gray = precomputed.gray
    
    # Look for periodic patterns at common upsampling ratios
    h, w = gray.shape
    
    # 2D FFT for periodic pattern detection
    magnitude = precomputed.fft_magnitude
    log_magnitude = precomputed.fft_log_magnitude
    
    cy, cx = h // 2, w // 2
    
    # Check for peaks at specific frequencies (upsampling artifacts)
    # Common upsampling ratios: 2x, 4x
    upsampling_peaks = []
    
    for ratio in [2, 4, 8]:
        # Expected peak locations for upsampling by ratio
        freq_h = h // ratio
        freq_w = w // ratio
        
        if freq_h > 5 and freq_w > 5:
            # Check for peaks at these frequencies
            region_h = max(3, freq_h // 10)
            region_w = max(3, freq_w // 10)
            
            # Top-right quadrant peak
            region1 = log_magnitude[cy-freq_h-region_h:cy-freq_h+region_h, 
                                    cx+freq_w-region_w:cx+freq_w+region_w]
            
            # Bottom-left quadrant peak  
            region2 = log_magnitude[cy+freq_h-region_h:cy+freq_h+region_h,
                                    cx-freq_w-region_w:cx-freq_w+region_w]
            
            if region1.size > 0 and region2.size > 0:
                peak_strength = (np.max(region1) + np.max(region2)) / 2
                
                # Compare to surrounding area
                surrounding_mean = np.mean(log_magnitude)
                surrounding_std = np.std(log_magnitude)
                
                normalized_peak = (peak_strength - surrounding_mean) / (surrounding_std + 1e-10)
                upsampling_peaks.append(normalized_peak)
                
                features[f'upsample_{ratio}x_peak'] = float(normalized_peak)
    
    if upsampling_peaks:
        features['upsample_max_peak'] = float(max(upsampling_peaks))
        features['upsample_mean_peak'] = float(np.mean(upsampling_peaks))
    else:
        features['upsample_max_peak'] = 0.0
        features['upsample_mean_peak'] = 0.0
    
    # Check for grid patterns (another upsampling signature)
    # Autocorrelation using FFT (much faster than signal.correlate2d)
    crop_h, crop_w = min(256, h), min(256, w)
    gray_crop = gray[:crop_h, :crop_w]
    template_h, template_w = min(64, h // 4), min(64, w // 4)
    
    # FFT-based correlation (O(n log n) instead of O(n^2))
    pad_shape = (crop_h + template_h - 1, crop_w + template_w - 1)
    fft_full = _fast_fft2(gray_crop, s=pad_shape)
    fft_template = _fast_fft2(gray[:template_h, :template_w], s=pad_shape)
    autocorr = np.real(_fast_ifft2(fft_full * np.conj(fft_template)))
    autocorr = autocorr[:crop_h - template_h + 1, :crop_w - template_w + 1]
    
    # Normalize autocorrelation
    autocorr_norm = autocorr / (np.max(autocorr) + 1e-10)
    
    # Look for periodic peaks using vectorized operations
    autocorr_1d = autocorr_norm.ravel()
    if len(autocorr_1d) > 2:
        # Vectorized peak detection
        peaks_mask = (autocorr_1d[1:-1] > autocorr_1d[:-2]) & (autocorr_1d[1:-1] > autocorr_1d[2:]) & (autocorr_1d[1:-1] > 0.3)
        peaks = autocorr_1d[1:-1][peaks_mask]
        features['upsample_autocorr_peaks'] = float(len(peaks))
        features['upsample_autocorr_max'] = float(np.max(peaks)) if len(peaks) > 0 else 0.0
    else:
        features['upsample_autocorr_peaks'] = 0.0
        features['upsample_autocorr_max'] = 0.0
    
    return features


def compute_color_bleeding_features(
    img_array: np.ndarray,
    *,
    gray: Optional[np.ndarray] = None,
    gray_edges: Optional[np.ndarray] = None,
    precomputed: Optional["ImagePrecomputedData"] = None,
) -> Dict[str, float]:
    """
    Detect color bleeding artifacts common in AI images.
    
    AI often has colors that "bleed" incorrectly across edges.
    """
    features = {}
    
    # Compute edges for each channel
    edges = []
    if HAS_CV2:
        for c in range(3):
            channel = img_array[:, :, c].astype(np.float32)
            sx = cv2.Sobel(channel, cv2.CV_32F, 1, 0, ksize=3)
            sy = cv2.Sobel(channel, cv2.CV_32F, 0, 1, ksize=3)
            sobel = np.sqrt(sx ** 2 + sy ** 2)
            edges.append(sobel)
    else:
        for c in range(3):
            channel = img_array[:, :, c].astype(np.float32)
            sobel = np.sqrt(gpu_sobel(channel, 0) ** 2 + gpu_sobel(channel, 1) ** 2)
            edges.append(sobel)
    
    # Edge correlation between channels
    edge_r, edge_g, edge_b = edges[0].ravel(), edges[1].ravel(), edges[2].ravel()
    
    features['color_bleed_edge_corr_rg'] = float(_fast_corrcoef(edge_r, edge_g)) if len(edge_r) > 1 else 0.0
    features['color_bleed_edge_corr_rb'] = float(_fast_corrcoef(edge_r, edge_b)) if len(edge_r) > 1 else 0.0
    features['color_bleed_edge_corr_gb'] = float(_fast_corrcoef(edge_g, edge_b)) if len(edge_g) > 1 else 0.0
    
    # Average edge alignment (should be high for real images)
    avg_edge_corr = (features['color_bleed_edge_corr_rg'] + 
                    features['color_bleed_edge_corr_rb'] + 
                    features['color_bleed_edge_corr_gb']) / 3
    features['color_bleed_edge_alignment'] = float(avg_edge_corr)
    
    # Check for color bleeding at strong edges
    gray = precomputed.gray
    gray_edges = precomputed.edges
    
    # Strong edge mask
    _flat_ge = gray_edges.ravel()
    _idx90 = int(0.90 * (len(_flat_ge) - 1))
    edge_threshold = np.partition(_flat_ge, _idx90)[_idx90]
    strong_edge_mask = gray_edges > edge_threshold
    
    # Compute color variance at edges vs non-edges
    for c, name in enumerate(['r', 'g', 'b']):
        channel = img_array[:, :, c].astype(np.float32)
        
        edge_var = np.var(channel[strong_edge_mask]) if np.sum(strong_edge_mask) > 0 else 0
        non_edge_var = np.var(channel[~strong_edge_mask]) if np.sum(~strong_edge_mask) > 0 else 0
        
        features[f'color_bleed_{name}_edge_var'] = float(edge_var)
        features[f'color_bleed_{name}_ratio'] = float(edge_var / (non_edge_var + 1e-10))
    
    return features


def compute_edge_chroma_bleed_features(
    img_array: np.ndarray,
    *,
    gray: Optional[np.ndarray] = None,
    edge_mag: Optional[np.ndarray] = None,
    precomputed: Optional["ImagePrecomputedData"] = None,
) -> Dict[str, float]:
    """
    Background color bleeding into foreground edges (chroma gradients near edges).
    """
    features = {
        'edge_chroma_bleed_ratio': 0.0,
        'edge_chroma_bleed_std': 0.0,
    }

    if img_array.ndim != 3 or img_array.shape[2] < 3:
        return features

    gray = precomputed.gray
    ycbcr = precomputed.ycbcr
    cb = ycbcr[:, :, 1]
    cr = ycbcr[:, :, 2]

    # Edge mask from luminance
    edge_mag = precomputed.edges
    _flat_em2 = edge_mag.ravel()
    _idx90 = int(0.90 * (len(_flat_em2) - 1))
    thresh = np.partition(_flat_em2, _idx90)[_idx90]
    edge_mask = edge_mag > thresh
    if np.sum(edge_mask) < 10:
        return features

    # Chroma gradients
    if HAS_CV2:
        cb32 = cb.astype(np.float32)
        cr32 = cr.astype(np.float32)
        cb_gx = cv2.Sobel(cb32, cv2.CV_32F, 1, 0, ksize=3)
        cb_gy = cv2.Sobel(cb32, cv2.CV_32F, 0, 1, ksize=3)
        cr_gx = cv2.Sobel(cr32, cv2.CV_32F, 1, 0, ksize=3)
        cr_gy = cv2.Sobel(cr32, cv2.CV_32F, 0, 1, ksize=3)
    else:
        cb_gx = gpu_sobel(cb, axis=1)
        cb_gy = gpu_sobel(cb, axis=0)
        cr_gx = gpu_sobel(cr, axis=1)
        cr_gy = gpu_sobel(cr, axis=0)
    chroma_grad = np.sqrt(cb_gx ** 2 + cb_gy ** 2 + cr_gx ** 2 + cr_gy ** 2)

    edge_vals = chroma_grad[edge_mask]
    all_vals = chroma_grad.ravel()
    features['edge_chroma_bleed_ratio'] = float(np.mean(edge_vals) / (np.mean(all_vals) + 1e-10))
    features['edge_chroma_bleed_std'] = float(np.std(edge_vals))

    return features


def compute_boundary_ghosting_features(
    img_array: np.ndarray,
    *,
    gray: Optional[np.ndarray] = None,
    precomputed: Optional["ImagePrecomputedData"] = None,
) -> Dict[str, float]:
    """
    Pattern ghosting near boundaries (border vs adjacent band correlation).
    """
    features = {
        'boundary_ghost_corr': 0.0,
        'boundary_hf_ratio': 0.0,
    }

    if gray is None:
        gray = precomputed.gray
    h, w = gray.shape
    if min(h, w) < 32:
        return features

    # High-pass
    low = precomputed.gaussian_2
    hp = gray - low

    band = max(4, min(12, min(h, w) // 20))
    # Border bands and adjacent interior bands
    top = hp[:band, :]
    top_in = hp[band:2*band, :]
    bottom = hp[-band:, :]
    bottom_in = hp[-2*band:-band, :]
    left = hp[:, :band]
    left_in = hp[:, band:2*band]
    right = hp[:, -band:]
    right_in = hp[:, -2*band:-band]

    def corr(a: np.ndarray, b: np.ndarray) -> float:
        a = a.ravel()
        b = b.ravel()
        if a.size == 0 or b.size == 0:
            return 0.0
        a = a - np.mean(a)
        b = b - np.mean(b)
        denom = (np.std(a) * np.std(b)) + 1e-10
        return float(np.mean(a * b) / denom)

    corrs = [
        corr(top, top_in),
        corr(bottom, bottom_in),
        corr(left, left_in),
        corr(right, right_in),
    ]
    features['boundary_ghost_corr'] = float(np.mean(corrs))

    # HF energy ratio border vs interior
    border_energy = np.mean(np.abs(np.concatenate([top.ravel(), bottom.ravel(), left.ravel(), right.ravel()])))
    interior = hp[band:-band, band:-band]
    interior_energy = np.mean(np.abs(interior)) if interior.size else 0.0
    features['boundary_hf_ratio'] = float(border_energy / (interior_energy + 1e-10))

    return features


def extract_gradient_features(
    img_array: np.ndarray,
    *,
    precomputed: Optional["ImagePrecomputedData"] = None,
) -> Dict[str, float]:
    """
    Extract all gradient and artifact features.

    Args:
        img_array: RGB image as numpy array (H, W, 3), uint8
        precomputed: Optional precomputed image data for sharing across modules

    Returns:
        Dictionary of feature names to values
    """
    features = {}

    # Use precomputed values
    gray = precomputed.gray
    edge_mag = precomputed.edges

    # Differential convolution features
    features.update(compute_differential_convolutions(img_array, gray=gray, precomputed=precomputed))
    
    # Pixel-level inconsistency features
    features.update(compute_pixel_inconsistency_features(img_array, gray=gray, precomputed=precomputed))
    
    # Artifact location features
    features.update(compute_artifact_location_features(img_array, gray=gray, edge_mag=edge_mag, precomputed=precomputed))
    
    # Upsampling artifact features
    features.update(compute_upsampling_artifact_features(img_array, gray=gray, precomputed=precomputed))
    
    # Color bleeding features
    features.update(compute_color_bleeding_features(img_array, gray=gray, gray_edges=edge_mag, precomputed=precomputed))

    # Chroma bleed near edges
    features.update(compute_edge_chroma_bleed_features(img_array, gray=gray, edge_mag=edge_mag, precomputed=precomputed))

    # Boundary ghosting features
    features.update(compute_boundary_ghosting_features(img_array, gray=gray, precomputed=precomputed))
    
    return features
