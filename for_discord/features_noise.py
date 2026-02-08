"""
features_noise.py - Noise residual and sensor pattern features for AI detection.

Analyzes noise patterns in images to distinguish real camera photos
from AI-generated images. Real photos have sensor noise; AI images don't.
"""

import numpy as np
from scipy import ndimage
from typing import Dict, Tuple, Optional, TYPE_CHECKING
from utils import fast_corrcoef as _corrcoef, fast_skew as _fast_skew, fast_kurt as _fast_kurt

try:
    from gpu_utils import (
        gpu_gaussian_filter,
        gpu_gaussian_filter_multi,
        gpu_fft2,
        gpu_fftshift,
        gpu_fft_magnitude_shifted_batch,
    )
    _HAS_GPU_UTILS = True
except ImportError:
    _HAS_GPU_UTILS = False

if TYPE_CHECKING:
    from image_precomputed import ImagePrecomputedData

# Try to import JIT utilities for acceleration
try:
    from jit_utils import fast_skewness, fast_kurtosis, fast_corrcoef, fast_entropy
    HAS_JIT = True
except ImportError:
    HAS_JIT = False


# _corrcoef, _fast_skew, _fast_kurt imported from utils.py above


def estimate_noise_residual(gray: np.ndarray, method: str = 'bilateral') -> np.ndarray:
    """
    Estimate noise residual by subtracting denoised version.
    
    Args:
        gray: Grayscale image
        method: Denoising method ('bilateral', 'median', 'gaussian')
        
    Returns:
        Noise residual image
    """
    try:
        import cv2
        _HAS_CV2 = True
    except ImportError:
        _HAS_CV2 = False
    
    if method == 'bilateral':
        if _HAS_CV2:
            # Faster params: d=5 instead of 9 (3x faster, similar quality)
            denoised = cv2.bilateralFilter(gray.astype(np.float32), 5, 50, 50)
        elif _HAS_GPU_UTILS:
            denoised = gpu_gaussian_filter(gray, sigma=1.5)
        else:
            denoised = ndimage.gaussian_filter(gray, sigma=1.5)
    elif method == 'median':
        if _HAS_CV2:
            denoised = cv2.medianBlur(gray.astype(np.uint8), 3).astype(np.float32)
        else:
            denoised = ndimage.median_filter(gray, size=3)
    else:  # gaussian
        if _HAS_CV2:
            # cv2.GaussianBlur is 6x faster than scipy gaussian_filter
            denoised = cv2.GaussianBlur(gray.astype(np.float32), (0, 0), 1.5)
        elif _HAS_GPU_UTILS:
            denoised = gpu_gaussian_filter(gray, sigma=1.5)
        else:
            denoised = ndimage.gaussian_filter(gray, sigma=1.5)
    
    residual = gray - denoised
    return residual


def compute_noise_statistics(residual: np.ndarray) -> Dict[str, float]:
    """
    Compute statistical features from noise residual.
    Optimized using np.partition for O(n) percentiles.
    """
    features = {}
    
    flat = residual.ravel()
    n = len(flat)
    
    # Basic statistics - compute in one pass
    mean_val = np.mean(flat)
    std_val = np.std(flat)
    features['noise_mean'] = float(mean_val)
    features['noise_std'] = float(std_val)
    features['noise_variance'] = float(std_val * std_val)
    features['noise_skewness'] = float(_fast_skew(flat))
    features['noise_kurtosis'] = float(_fast_kurt(flat))
    
    # Absolute noise statistics
    abs_residual = np.abs(flat)
    features['noise_abs_mean'] = float(np.mean(abs_residual))
    features['noise_abs_std'] = float(np.std(abs_residual))
    
    # Optimized percentiles using np.partition (O(n) instead of O(n log n))
    p5_idx = max(0, int(0.05 * n) - 1)
    p25_idx = max(0, int(0.25 * n) - 1)
    p75_idx = min(n - 1, int(0.75 * n))
    p95_idx = min(n - 1, int(0.95 * n))
    
    # Single partition call for all percentiles
    indices = sorted(set([p5_idx, p25_idx, p75_idx, p95_idx]))
    partitioned = np.partition(flat, indices)
    
    features['noise_p5'] = float(partitioned[p5_idx])
    features['noise_p25'] = float(partitioned[p25_idx])
    features['noise_p75'] = float(partitioned[p75_idx])
    features['noise_p95'] = float(partitioned[p95_idx])
    features['noise_iqr'] = features['noise_p75'] - features['noise_p25']
    
    return features


def compute_spatial_noise_features(residual: np.ndarray, block_size: int = 32) -> Dict[str, float]:
    """
    Analyze spatial distribution of noise.
    
    Real camera noise has consistent spatial patterns;
    AI noise is often more uniform or has different spatial structure.
    """
    features = {}
    
    h, w = residual.shape
    
    # Vectorized block statistics using reshape
    n_blocks_h = h // block_size
    n_blocks_w = w // block_size
    
    if n_blocks_h == 0 or n_blocks_w == 0:
        features['noise_var_variance'] = 0.0
        features['noise_var_mean'] = 0.0
        features['noise_var_cv'] = 0.0
        features['noise_block_mean_var'] = 0.0
        features['noise_spatial_consistency'] = 0.0
        return features
    
    # Crop to exact multiple of block_size and reshape
    cropped = residual[:n_blocks_h * block_size, :n_blocks_w * block_size]
    blocks = cropped.reshape(n_blocks_h, block_size, n_blocks_w, block_size)
    blocks = blocks.transpose(0, 2, 1, 3).reshape(-1, block_size, block_size)
    
    # Compute variance and mean for each block
    block_variances = np.var(blocks, axis=(1, 2))
    block_means = np.mean(blocks, axis=(1, 2))
    
    # Variance of variances (should be consistent for real photos)
    features['noise_var_variance'] = float(np.var(block_variances))
    features['noise_var_mean'] = float(np.mean(block_variances))
    features['noise_var_cv'] = float(np.std(block_variances) / (np.mean(block_variances) + 1e-10))
    
    # Mean of block means (should be close to 0 for good denoising)
    features['noise_block_mean_var'] = float(np.var(block_means))
    
    # Spatial consistency ratio
    if features['noise_var_mean'] > 0:
        features['noise_spatial_consistency'] = float(1.0 / (features['noise_var_cv'] + 1))
    else:
        features['noise_spatial_consistency'] = 0.0
    
    return features


def compute_channel_noise_correlation(img_array: np.ndarray, *, precomputed=None) -> Dict[str, float]:
    """
    Analyze noise correlation between color channels.
    
    Real camera sensors have correlated noise patterns due to:
    - Shared electronics
    - Demosaicing
    - Color filter array
    
    AI images typically have independent noise per channel.
    """
    features = {}
    
    # Compute residuals for each channel — use precomputed cache when available
    residuals = []
    for i in range(3):
        if precomputed is not None:
            residual = precomputed.get_channel_residual(i, 1.5)
        else:
            gray = img_array[:, :, i].astype(np.float32)
            residual = estimate_noise_residual(gray, method='gaussian')
        residuals.append(residual.ravel())
    
    # Inter-channel correlations - batch computation
    features['noise_corr_rg'] = _corrcoef(residuals[0], residuals[1])
    features['noise_corr_rb'] = _corrcoef(residuals[0], residuals[2])
    features['noise_corr_gb'] = _corrcoef(residuals[1], residuals[2])
    
    # Average inter-channel correlation
    correlations = [features['noise_corr_rg'], features['noise_corr_rb'], features['noise_corr_gb']]
    features['noise_avg_channel_corr'] = float(np.mean(correlations))
    
    # Channel noise variance ratios
    variances = [np.var(r) for r in residuals]
    features['noise_var_r'] = float(variances[0])
    features['noise_var_g'] = float(variances[1])
    features['noise_var_b'] = float(variances[2])
    
    # Green channel typically has lower noise in real cameras
    if variances[0] + variances[2] > 0:
        features['noise_g_rb_ratio'] = float(variances[1] / ((variances[0] + variances[2]) / 2 + 1e-10))
    else:
        features['noise_g_rb_ratio'] = 1.0
    
    return features


def compute_prnu_like_features(
    gray: np.ndarray,
    *,
    precomputed: Optional["ImagePrecomputedData"] = None,
) -> Dict[str, float]:
    """
    Compute features similar to Photo Response Non-Uniformity (PRNU) analysis.
    
    PRNU is a sensor fingerprint present in real camera photos.
    AI images lack this consistent pattern.
    """
    features = {}
    
    # Estimate PRNU-like pattern using shared caches when available.
    smoothed_1 = precomputed.gaussian_1
    smoothed_3 = precomputed.gaussian_3
    smoothed_5 = precomputed.gaussian_5
    
    hf_1 = gray - smoothed_1
    hf_3 = smoothed_1 - smoothed_3
    hf_5 = smoothed_3 - smoothed_5
    
    # Correlation between scales (consistent for real PRNU)
    features['prnu_scale_corr_1_3'] = _corrcoef(hf_1, hf_3)
    features['prnu_scale_corr_3_5'] = _corrcoef(hf_3, hf_5)
    
    # Energy ratio across scales
    e1 = np.sum(hf_1**2)
    e3 = np.sum(hf_3**2)
    e5 = np.sum(hf_5**2)
    
    features['prnu_energy_1'] = float(e1)
    features['prnu_energy_3'] = float(e3)
    features['prnu_energy_5'] = float(e5)
    features['prnu_energy_ratio'] = float(e1 / (e3 + 1e-10))
    
    return features


def compute_demosaicing_features(img_array: np.ndarray) -> Dict[str, float]:
    """
    Detect demosaicing artifacts characteristic of real camera images.
    
    Real cameras use Bayer filter arrays, leading to specific
    interpolation patterns. AI images don't have these.
    """
    features = {}
    
    # Check for periodic patterns at Bayer frequency
    magnitude_batch = None
    if _HAS_GPU_UTILS and img_array.ndim == 3 and img_array.shape[2] >= 3:
        # Batch FFT for the 3 color channels to reduce kernel launches / transfers.
        channels = img_array[:, :, :3].transpose(2, 0, 1).astype(np.float64, copy=False)
        magnitude_batch = gpu_fft_magnitude_shifted_batch(channels)

    for i, channel_name in enumerate(['r', 'g', 'b']):
        channel = img_array[:, :, i].astype(np.float64, copy=False)

        if magnitude_batch is not None:
            magnitude = magnitude_batch[i]
        else:
            # Compute 2D FFT (CPU or per-channel GPU fallback)
            f = gpu_fft2(channel) if _HAS_GPU_UTILS else np.fft.fft2(channel)
            f_shift = gpu_fftshift(f) if _HAS_GPU_UTILS else np.fft.fftshift(f)
            magnitude = np.abs(f_shift)
        
        h, w = magnitude.shape
        cy, cx = h // 2, w // 2
        
        # Check for peaks at Nyquist/2 (Bayer pattern frequency)
        # These would appear at (cy±h/4, cx) and (cy, cx±w/4)
        quarter_h, quarter_w = h // 4, w // 4
        
        # Sample regions at expected demosaic artifact locations
        region_size = 5
        
        # Horizontal artifact location
        h_artifact_region = magnitude[cy-region_size:cy+region_size, cx+quarter_w-region_size:cx+quarter_w+region_size]
        # Vertical artifact location
        v_artifact_region = magnitude[cy+quarter_h-region_size:cy+quarter_h+region_size, cx-region_size:cx+region_size]
        
        # Background reference (should be lower)
        bg_region = magnitude[cy+quarter_h//2-region_size:cy+quarter_h//2+region_size, 
                             cx+quarter_w//2-region_size:cx+quarter_w//2+region_size]
        
        h_artifact_energy = np.mean(h_artifact_region) if h_artifact_region.size > 0 else 0
        v_artifact_energy = np.mean(v_artifact_region) if v_artifact_region.size > 0 else 0
        bg_energy = np.mean(bg_region) if bg_region.size > 0 else 1
        
        features[f'demosaic_{channel_name}_h_artifact'] = float(h_artifact_energy / (bg_energy + 1e-10))
        features[f'demosaic_{channel_name}_v_artifact'] = float(v_artifact_energy / (bg_energy + 1e-10))
    
    # Green channel interpolation pattern
    # In Bayer arrays, green has a checkerboard pattern
    g_channel = img_array[:, :, 1].astype(np.float32)
    
    # Check for checkerboard correlation
    h, w = g_channel.shape
    checkerboard = np.indices((h, w)).sum(axis=0) % 2
    
    # Correlation with checkerboard pattern
    g_flat = g_channel.ravel()
    cb_flat = checkerboard.ravel().astype(np.float32)
    features['demosaic_checkerboard_corr'] = abs(_corrcoef(g_flat, cb_flat))
    
    return features


def compute_noise_level_function(img_array: np.ndarray, *, precomputed=None) -> Dict[str, float]:
    """
    Estimate noise level as function of intensity.
    
    Real camera noise follows specific patterns:
    - Shot noise (Poisson) dominates at high intensities
    - Read noise dominates at low intensities
    
    AI images often have uniform or incorrect noise models.
    """
    features = {}
    
    if precomputed is not None:
        gray = precomputed.gray
    else:
        gray = np.mean(img_array, axis=2)
    residual = estimate_noise_residual(gray, method='gaussian')
    
    # Vectorized binning by intensity
    n_bins = 10
    # Digitize intensity values into bins
    gray_flat = gray.ravel()
    residual_flat = residual.ravel()
    
    # Assign each pixel to a bin (0 to n_bins-1)
    bin_indices = np.clip((gray_flat / 256 * n_bins).astype(np.int32), 0, n_bins - 1)
    
    # Compute stats per bin using groupby-style operations
    noise_vs_intensity = np.full(n_bins, np.nan)
    intensity_centers = np.arange(n_bins) * 25.5 + 12.75  # Bin centers
    
    for i in range(n_bins):
        mask = bin_indices == i
        count = np.sum(mask)
        if count > 100:
            noise_vs_intensity[i] = np.std(residual_flat[mask])
    
    valid = ~np.isnan(noise_vs_intensity)
    
    if np.sum(valid) >= 3:
        # Fit linear model (shot noise should give sqrt relationship)
        valid_intensities = intensity_centers[valid]
        valid_noise = noise_vs_intensity[valid]
        
        # Linear fit
        slope, intercept = np.polyfit(valid_intensities, valid_noise, 1)
        features['noise_intensity_slope'] = float(slope)
        features['noise_intensity_intercept'] = float(intercept)
        
        # Fit residual (how well does it follow expected pattern)
        predicted = slope * valid_intensities + intercept
        fit_residual = np.mean((valid_noise - predicted)**2)
        features['noise_model_fit_error'] = float(fit_residual)
        
        # Ratio of low to high intensity noise
        features['noise_low_high_ratio'] = float(valid_noise[0] / (valid_noise[-1] + 1e-10))
    else:
        features['noise_intensity_slope'] = 0.0
        features['noise_intensity_intercept'] = 0.0
        features['noise_model_fit_error'] = 0.0
        features['noise_low_high_ratio'] = 1.0
    
    return features


def _spatial_autocorr(residual: np.ndarray) -> float:
    """Fast spatial autocorrelation (lag-1) for residual."""
    if residual.size < 4:
        return 0.0
    r = residual
    r_mean = np.mean(r)
    r = r - r_mean
    var = np.mean(r ** 2)
    if var < 1e-10:
        return 0.0
    # lag-1 correlations (horizontal, vertical, diagonal)
    corr_h = np.mean(r[:, 1:] * r[:, :-1])
    corr_v = np.mean(r[1:, :] * r[:-1, :])
    corr_d = np.mean(r[1:, 1:] * r[:-1, :-1])
    return float((corr_h + corr_v + corr_d) / (3.0 * var + 1e-10))


def compute_multiscale_channel_residual_features(
    img_array: np.ndarray,
    sigmas: Tuple[float, float] = (1.0, 2.0),
    *,
    precomputed=None,
) -> Dict[str, float]:
    """
    Multi-scale, per-channel residual stats.
    
    Computes variance, skew, kurtosis, and spatial autocorrelation
    of denoise residuals per channel and per scale.
    """
    features: Dict[str, float] = {}

    if img_array.ndim != 3 or img_array.shape[2] < 3:
        return features

    h, w = img_array.shape[:2]
    # Downsample for speed on large images
    step = max(1, max(h, w) // 512)

    for c, name in enumerate(['r', 'g', 'b']):
        channel = img_array[:, :, c].astype(np.float32)
        if step > 1:
            channel = channel[::step, ::step]

        for sigma in sigmas:
            # Use precomputed channel residual cache when not downsampled
            if precomputed is not None and step == 1:
                residual = precomputed.get_channel_residual(c, sigma)
            elif _HAS_GPU_UTILS:
                denoised = gpu_gaussian_filter(channel, sigma=sigma)
                residual = channel - denoised
            else:
                denoised = ndimage.gaussian_filter(channel, sigma=sigma)
                residual = channel - denoised
            flat = residual.ravel()

            features[f'noise_{name}_s{int(sigma*10)}_var'] = float(np.var(flat))
            features[f'noise_{name}_s{int(sigma*10)}_skew'] = float(_fast_skew(flat))
            features[f'noise_{name}_s{int(sigma*10)}_kurt'] = float(_fast_kurt(flat))
            features[f'noise_{name}_s{int(sigma*10)}_autocorr'] = float(_spatial_autocorr(residual))

    return features


def compute_jpeg_noise_features(img_array: np.ndarray, *, precomputed=None) -> Dict[str, float]:
    """
    Analyze JPEG-like noise patterns.
    
    Even in PNG images, previous JPEG compression leaves traces.
    AI images have different JPEG artifact patterns.
    """
    features = {}
    
    if precomputed is not None:
        gray = precomputed.gray
    else:
        gray = np.mean(img_array, axis=2).astype(np.float32, copy=False)

    # Phase-invariant 8x8 boundary detection: cropping can offset the block grid.
    try:
        from blockgrid import best_periodic_boundary_stats
        row_diffs, col_diffs = precomputed.boundary_diffs
        block_size = 8

        rb, ri, _, _, _ = best_periodic_boundary_stats(row_diffs, block_size)
        cb, ci, _, _, _ = best_periodic_boundary_stats(col_diffs, block_size)

        boundary_mean = float((rb + cb) / 2.0)
        interior_mean = float((ri + ci) / 2.0)

        if interior_mean <= 1e-10:
            ratio = 1.0
        else:
            ratio = float(boundary_mean / (interior_mean + 1e-10))

        features['jpeg_boundary_grad_mean'] = boundary_mean
        features['jpeg_interior_grad_mean'] = interior_mean
        features['jpeg_blocking_ratio'] = ratio
    except Exception:
        # Conservative fallback to stable defaults (never crash extraction).
        features['jpeg_boundary_grad_mean'] = 0.0
        features['jpeg_interior_grad_mean'] = 0.0
        features['jpeg_blocking_ratio'] = 1.0
    
    return features


def extract_noise_features(
    img_array: np.ndarray,
    *,
    precomputed: Optional["ImagePrecomputedData"] = None,
) -> Dict[str, float]:
    """
    Extract all noise-related features from an image.

    Args:
        img_array: RGB image as numpy array (H, W, 3), uint8
        precomputed: Optional precomputed image data for sharing across modules

    Returns:
        Dictionary of feature names to values
    """
    features = {}

    # Use precomputed grayscale and noise residual
    gray = precomputed.gray
    residual = precomputed.noise_residual
    
    # Basic noise statistics
    features.update(compute_noise_statistics(residual))
    
    # Spatial noise features
    features.update(compute_spatial_noise_features(residual))
    
    # Channel correlation features
    features.update(compute_channel_noise_correlation(img_array, precomputed=precomputed))
    
    # PRNU-like features
    features.update(compute_prnu_like_features(gray, precomputed=precomputed))
    
    # Demosaicing features
    features.update(compute_demosaicing_features(img_array))
    
    # Noise level function
    features.update(compute_noise_level_function(img_array, precomputed=precomputed))
    
    # JPEG noise features
    features.update(compute_jpeg_noise_features(img_array, precomputed=precomputed))

    # Multi-scale channel residual stats
    features.update(compute_multiscale_channel_residual_features(img_array, precomputed=precomputed))
    
    return features
