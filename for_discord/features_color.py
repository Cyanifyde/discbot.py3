"""
features_color.py - Color and tone statistics for AI detection.

Extracts statistical features from color channels that can reveal
AI-generated image characteristics.

OPTIMIZED: Uses JIT-compiled functions where available.
"""

import numpy as np
from typing import Dict, List, Tuple, Optional, TYPE_CHECKING
from PIL import Image

if TYPE_CHECKING:
    from image_precomputed import ImagePrecomputedData

# Optional OpenCV acceleration
try:
    import cv2
    HAS_CV2 = True
except ImportError:
    HAS_CV2 = False

# Try to import JIT utilities
try:
    from jit_utils import fast_moments
    HAS_JIT = True
except ImportError:
    HAS_JIT = False

from utils import fast_skew, fast_kurt, fast_entropy_hist


def compute_histogram_moments(channel: np.ndarray) -> Dict[str, float]:
    """
    Compute statistical moments of a channel histogram.
    
    AI images often have different histogram characteristics:
    - Unusual clustering around certain values
    - Less natural variation in tonal distribution
    
    OPTIMIZED: Uses JIT when available, histogram-based percentiles for uint8 data.
    """
    flat = channel.ravel()
    n = len(flat)
    
    if n == 0:
        return {
            'mean': 0.0, 'std': 0.0, 'skewness': 0.0, 'kurtosis': 0.0,
            'min': 0.0, 'max': 0.0, 'median': 0.0, 'iqr': 0.0,
        }
    
    flat_f = flat.astype(np.float64) if flat.dtype != np.float64 else flat
    
    if HAS_JIT:
        mean_val, std_val, skewness, kurtosis = fast_moments(flat_f)
    else:
        mean_val = np.mean(flat_f)
        std_val = np.std(flat_f)
        
        if std_val < 1e-10:
            skewness = 0.0
            kurtosis = 0.0
        else:
            centered = flat_f - mean_val
            m2 = np.mean(centered ** 2)
            m3 = np.mean(centered ** 3)
            m4 = np.mean(centered ** 4)
            skewness = float(m3 / (m2 ** 1.5 + 1e-10))
            kurtosis = float(m4 / (m2 ** 2 + 1e-10) - 3.0)
    
    # Fast histogram-based percentiles for uint8 data (O(n) vs O(n log n))
    if flat.dtype == np.uint8:
        hist = np.bincount(flat, minlength=256)
        cumsum = np.cumsum(hist)
        p25 = float(np.searchsorted(cumsum, n * 0.25))
        p50 = float(np.searchsorted(cumsum, n * 0.50))
        p75 = float(np.searchsorted(cumsum, n * 0.75))
        vmin = float(np.argmax(hist > 0))
        vmax = float(255 - np.argmax(hist[::-1] > 0))
    else:
        p25_idx = int(0.25 * (n - 1))
        p50_idx = int(0.50 * (n - 1))
        p75_idx = int(0.75 * (n - 1))
        indices = [0, p25_idx, p50_idx, p75_idx, n - 1]
        partitioned = np.partition(flat_f, indices)
        vmin, p25, p50, p75, vmax = float(partitioned[0]), float(partitioned[p25_idx]), float(partitioned[p50_idx]), float(partitioned[p75_idx]), float(partitioned[-1])
    
    return {
        'mean': float(mean_val),
        'std': float(std_val),
        'skewness': float(skewness),
        'kurtosis': float(kurtosis),
        'min': vmin,
        'max': vmax,
        'median': p50,
        'iqr': p75 - p25,
    }


def compute_rgb_features(
    img_array: np.ndarray,
    *,
    precomputed: Optional["ImagePrecomputedData"] = None,
) -> Dict[str, float]:
    """
    Extract features from RGB color space.
    
    AI images often show:
    - Unusual inter-channel correlations
    - Different noise patterns per channel
    - Abnormal color distributions
    
    OPTIMIZED: Reduced redundant computations and type conversions.
    """
    features = {}
    
    # Pre-flatten channels once (avoid repeated flattening)
    h, w, _ = img_array.shape
    if precomputed is not None:
        r = precomputed.r.ravel()
        g = precomputed.g.ravel()
        b = precomputed.b.ravel()
        r_u8 = img_array[:, :, 0].ravel()
        g_u8 = img_array[:, :, 1].ravel()
        b_u8 = img_array[:, :, 2].ravel()
    else:
        r_u8 = img_array[:, :, 0].ravel()
        g_u8 = img_array[:, :, 1].ravel()
        b_u8 = img_array[:, :, 2].ravel()
        r = r_u8.astype(np.float64)
        g = g_u8.astype(np.float64)
        b = b_u8.astype(np.float64)
    
    # Per-channel statistics using JIT-optimized moments
    channels = [('r', r, r_u8), ('g', g, g_u8), ('b', b, b_u8)]
    for name, ch, ch_u8 in channels:
        if HAS_JIT:
            mean_val, std_val, skewness, kurtosis = fast_moments(ch)
        else:
            mean_val = np.mean(ch)
            std_val = np.std(ch)
            if std_val < 1e-10:
                skewness, kurtosis = 0.0, 0.0
            else:
                centered = ch - mean_val
                m2 = np.mean(centered ** 2)
                m3 = np.mean(centered ** 3)
                m4 = np.mean(centered ** 4)
                skewness = float(m3 / (m2 ** 1.5 + 1e-10))
                kurtosis = float(m4 / (m2 ** 2 + 1e-10) - 3.0)
        
        # Fast histogram-based percentiles for uint8 data
        if ch_u8.dtype == np.uint8:
            hist = np.bincount(ch_u8, minlength=256)
            cumsum = np.cumsum(hist)
            n = len(ch_u8)
            p25 = float(np.searchsorted(cumsum, n * 0.25))
            p50 = float(np.searchsorted(cumsum, n * 0.50))
            p75 = float(np.searchsorted(cumsum, n * 0.75))
            vmin = float(np.argmax(hist > 0))
            vmax = float(255 - np.argmax(hist[::-1] > 0))
        else:
            n = len(ch)
            p25_idx, p50_idx, p75_idx = int(0.25 * (n-1)), int(0.5 * (n-1)), int(0.75 * (n-1))
            partitioned = np.partition(ch, [0, p25_idx, p50_idx, p75_idx, n-1])
            vmin, p25, p50, p75, vmax = float(partitioned[0]), float(partitioned[p25_idx]), float(partitioned[p50_idx]), float(partitioned[p75_idx]), float(partitioned[-1])
        
        features[f'rgb_{name}_mean'] = float(mean_val)
        features[f'rgb_{name}_std'] = float(std_val)
        features[f'rgb_{name}_skewness'] = float(skewness)
        features[f'rgb_{name}_kurtosis'] = float(kurtosis)
        features[f'rgb_{name}_min'] = vmin
        features[f'rgb_{name}_max'] = vmax
        features[f'rgb_{name}_median'] = p50
        features[f'rgb_{name}_iqr'] = p75 - p25
        
        # CV computed inline
        if mean_val > 0:
            features[f'rgb_{name}_cv'] = float(std_val / mean_val)
        else:
            features[f'rgb_{name}_cv'] = 0.0
    
    # Fast correlation using dot products (faster than np.corrcoef for 3 channels)
    r_centered = r - np.mean(r)
    g_centered = g - np.mean(g)
    b_centered = b - np.mean(b)
    
    r_std = np.sqrt(np.dot(r_centered, r_centered))
    g_std = np.sqrt(np.dot(g_centered, g_centered))
    b_std = np.sqrt(np.dot(b_centered, b_centered))
    
    if r_std > 1e-10 and g_std > 1e-10:
        features['rgb_corr_rg'] = float(np.dot(r_centered, g_centered) / (r_std * g_std))
    else:
        features['rgb_corr_rg'] = 0.0
    
    if r_std > 1e-10 and b_std > 1e-10:
        features['rgb_corr_rb'] = float(np.dot(r_centered, b_centered) / (r_std * b_std))
    else:
        features['rgb_corr_rb'] = 0.0
        
    if g_std > 1e-10 and b_std > 1e-10:
        features['rgb_corr_gb'] = float(np.dot(g_centered, b_centered) / (g_std * b_std))
    else:
        features['rgb_corr_gb'] = 0.0
    
    # Channel differences (reuse already-flattened arrays)
    rg_diff = r - g
    rb_diff = r - b
    gb_diff = g - b
    
    features['rgb_diff_rg_std'] = float(np.std(rg_diff))
    features['rgb_diff_rb_std'] = float(np.std(rb_diff))
    features['rgb_diff_gb_std'] = float(np.std(gb_diff))
    
    # Total variance (reuse computed variances)
    features['rgb_total_variance'] = float(np.var(r) + np.var(g) + np.var(b))
    
    return features


def compute_hsv_features(img_array: np.ndarray) -> Dict[str, float]:
    """
    Extract features from HSV color space.
    
    AI images often exhibit:
    - Over-saturated or oddly clustered saturation
    - Unusual hue distributions
    - Different value (brightness) patterns
    """
    # Convert to HSV (use OpenCV if available for speed)
    if HAS_CV2:
        hsv = cv2.cvtColor(img_array, cv2.COLOR_RGB2HSV)
        hsv_array = hsv.astype(np.float64)
        # OpenCV hue is 0-179; scale to 0-255 for compatibility with prior ranges
        hsv_array[:, :, 0] = hsv_array[:, :, 0] * (255.0 / 179.0)
    else:
        from PIL import Image
        img = Image.fromarray(img_array)
        hsv = img.convert('HSV')
        hsv_array = np.array(hsv, dtype=np.float64)
    
    features = {}
    channel_names = ['h', 's', 'v']
    
    for i, name in enumerate(channel_names):
        moments = compute_histogram_moments(hsv_array[:, :, i])
        for key, value in moments.items():
            features[f'hsv_{name}_{key}'] = value
    
    # Saturation-specific features (AI often over-saturates)
    saturation = hsv_array[:, :, 1].ravel().astype(np.float64)
    
    # Percentage of high saturation pixels
    features['hsv_high_sat_ratio'] = float(np.mean(saturation > 200))
    features['hsv_low_sat_ratio'] = float(np.mean(saturation < 30))
    features['hsv_mid_sat_ratio'] = float(np.mean((saturation >= 30) & (saturation <= 200)))
    
    # Saturation clustering (AI tends to have saturation clusters)
    sat_hist, _ = np.histogram(saturation, bins=32, range=(0, 256))
    sat_hist = sat_hist / (sat_hist.sum() + 1e-10)
    features['hsv_sat_entropy'] = fast_entropy_hist(sat_hist)

    # Histogram gaps/spikes (human picker spikes vs AI smooth fill)
    sat_zero = float(np.sum(sat_hist == 0))
    features['hsv_sat_zero_bins'] = sat_zero
    features['hsv_sat_zero_ratio'] = float(sat_zero / 32.0)
    features['hsv_sat_hist_smoothness'] = float(np.mean(np.abs(np.diff(sat_hist))))
    features['hsv_sat_max_to_mean'] = float(np.max(sat_hist) / (np.mean(sat_hist) + 1e-10))
    
    # Hue distribution features
    hue = hsv_array[:, :, 0].ravel().astype(np.float64)
    hue_hist, _ = np.histogram(hue, bins=36, range=(0, 256))  # 10-degree bins
    hue_hist = hue_hist / (hue_hist.sum() + 1e-10)
    features['hsv_hue_entropy'] = fast_entropy_hist(hue_hist)

    hue_zero = float(np.sum(hue_hist == 0))
    features['hsv_hue_zero_bins'] = hue_zero
    features['hsv_hue_zero_ratio'] = float(hue_zero / 36.0)
    features['hsv_hue_hist_smoothness'] = float(np.mean(np.abs(np.diff(hue_hist))))
    features['hsv_hue_max_to_mean'] = float(np.max(hue_hist) / (np.mean(hue_hist) + 1e-10))
    
    # Dominant hue detection
    features['hsv_dominant_hue'] = float(np.argmax(hue_hist) * 10)
    features['hsv_hue_concentration'] = float(np.max(hue_hist))

    # Combined gap/spike proxy: higher = spikier / more gapped.
    features['hsv_hist_gap_spike_score'] = float(
        (features['hsv_hue_max_to_mean'] + features['hsv_sat_max_to_mean']) / 2.0
    )
    
    return features


def compute_ycbcr_features(img_array: np.ndarray) -> Dict[str, float]:
    """
    Extract features from YCbCr color space.
    
    Useful for detecting:
    - Chroma-luma correlations (different in AI vs real)
    - Color subsampling artifacts
    """
    # Convert to YCbCr (OpenCV is faster; returns YCrCb)
    if HAS_CV2:
        ycrcb = cv2.cvtColor(img_array, cv2.COLOR_RGB2YCrCb).astype(np.float64)
        # Reorder to Y, Cb, Cr
        ycbcr_array = np.stack([ycrcb[:, :, 0], ycrcb[:, :, 2], ycrcb[:, :, 1]], axis=2)
    else:
        from PIL import Image
        img = Image.fromarray(img_array)
        ycbcr = img.convert('YCbCr')
        ycbcr_array = np.array(ycbcr, dtype=np.float64)
    
    features = {}
    channel_names = ['y', 'cb', 'cr']
    
    for i, name in enumerate(channel_names):
        moments = compute_histogram_moments(ycbcr_array[:, :, i])
        for key, value in moments.items():
            features[f'ycbcr_{name}_{key}'] = value
    
    # Chroma-luma correlations - batch computation
    y = ycbcr_array[:, :, 0].ravel().astype(np.float64)
    cb = ycbcr_array[:, :, 1].ravel().astype(np.float64)
    cr = ycbcr_array[:, :, 2].ravel().astype(np.float64)
    
    ycbcr_stack = np.vstack([y, cb, cr])
    corr_matrix = np.corrcoef(ycbcr_stack)
    features['ycbcr_corr_y_cb'] = float(corr_matrix[0, 1])
    features['ycbcr_corr_y_cr'] = float(corr_matrix[0, 2])
    features['ycbcr_corr_cb_cr'] = float(corr_matrix[1, 2])
    
    # Chroma variance relative to luma
    y_var = np.var(y)
    cb_var = np.var(cb)
    cr_var = np.var(cr)
    
    if y_var > 0:
        features['ycbcr_cb_y_var_ratio'] = float(cb_var / y_var)
        features['ycbcr_cr_y_var_ratio'] = float(cr_var / y_var)
    else:
        features['ycbcr_cb_y_var_ratio'] = 0.0
        features['ycbcr_cr_y_var_ratio'] = 0.0
    
    return features


def compute_lab_features(img_array: np.ndarray) -> Dict[str, float]:
    """
    Extract features from LAB color space.
    
    LAB is perceptually uniform, making it good for detecting
    subtle color anomalies in AI-generated images.
    """
    try:
        if HAS_CV2:
            lab_cv = cv2.cvtColor(img_array, cv2.COLOR_RGB2LAB).astype(np.float64)
            # Convert OpenCV LAB to approximate skimage ranges
            l = lab_cv[:, :, 0] * (100.0 / 255.0)
            a = lab_cv[:, :, 1] - 128.0
            b = lab_cv[:, :, 2] - 128.0
            lab = np.stack([l, a, b], axis=2)
        else:
            from skimage import color
            lab = color.rgb2lab(img_array / 255.0)
    except ImportError:
        # Fallback: simple approximation
        return {}
    
    features = {}
    channel_names = ['l', 'a', 'b']
    
    for i, name in enumerate(channel_names):
        channel = lab[:, :, i]
        features[f'lab_{name}_mean'] = float(np.mean(channel))
        features[f'lab_{name}_std'] = float(np.std(channel))
        features[f'lab_{name}_skew'] = float(fast_skew(channel.ravel()))
        features[f'lab_{name}_kurt'] = float(fast_kurt(channel.ravel()))
    
    # Color gamut usage - use ravel (view) instead of flatten (copy)
    a_channel = lab[:, :, 1].ravel()
    b_channel = lab[:, :, 2].ravel()
    
    # Chromaticity spread - compute in-place to avoid allocation
    chroma = np.hypot(a_channel, b_channel)  # faster than sqrt(a**2 + b**2)
    features['lab_chroma_mean'] = float(np.mean(chroma))
    features['lab_chroma_std'] = float(np.std(chroma))
    features['lab_chroma_max'] = float(np.max(chroma))
    
    # Hue angle distribution
    hue_angle = np.arctan2(b_channel, a_channel + 1e-10)  # Avoid division by zero
    mean_sin = np.mean(np.sin(hue_angle))
    mean_cos = np.mean(np.cos(hue_angle))
    features['lab_hue_circular_mean'] = float(np.arctan2(mean_sin, mean_cos))
    
    # Circular std with safety checks to avoid sqrt of negative and log of zero
    r_value = np.sqrt(mean_cos**2 + mean_sin**2)
    r_value = np.clip(r_value, 1e-10, 1.0)  # Ensure valid range, avoid log(0)
    circ_std = np.sqrt(np.abs(-2 * np.log(r_value)))  # abs to handle floating point errors
    if np.isnan(circ_std) or np.isinf(circ_std):
        circ_std = 0.0
    features['lab_hue_circular_std'] = float(circ_std)
    
    return features


def compute_highlight_shadow_features(img_array: np.ndarray) -> Dict[str, float]:
    """
    Analyze highlight and shadow regions.
    
    AI images often have:
    - Unnatural highlight roll-off
    - Clipped highlights/shadows
    - Different tonal transitions
    """
    # Convert to grayscale for luminance analysis
    gray = np.mean(img_array, axis=2)
    
    features = {}
    
    # Fast histogram-based percentiles (gray values are in [0, 255])
    gray_u8 = np.round(gray).astype(np.uint8)
    hist_raw = np.bincount(gray_u8.ravel(), minlength=256)
    cumsum = np.cumsum(hist_raw)
    n = cumsum[-1]
    p1 = float(np.searchsorted(cumsum, n * 0.01))
    p5 = float(np.searchsorted(cumsum, n * 0.05))
    p10 = float(np.searchsorted(cumsum, n * 0.10))
    p90 = float(np.searchsorted(cumsum, n * 0.90))
    p95 = float(np.searchsorted(cumsum, n * 0.95))
    p99 = float(np.searchsorted(cumsum, n * 0.99))
    
    # Highlight analysis (top 5% brightness)
    highlight_threshold = p95
    highlights = gray[gray >= highlight_threshold]
    
    features['highlight_ratio'] = float(len(highlights) / gray.size)
    features['highlight_mean'] = float(np.mean(highlights)) if len(highlights) > 0 else 0.0
    features['highlight_std'] = float(np.std(highlights)) if len(highlights) > 0 else 0.0
    
    # Clipped highlights (completely white)
    features['clipped_highlight_ratio'] = float(np.mean(gray >= 254))
    
    # Shadow analysis (bottom 5% brightness)
    shadow_threshold = p5
    shadows = gray[gray <= shadow_threshold]
    
    features['shadow_ratio'] = float(len(shadows) / gray.size)
    features['shadow_mean'] = float(np.mean(shadows)) if len(shadows) > 0 else 0.0
    features['shadow_std'] = float(np.std(shadows)) if len(shadows) > 0 else 0.0
    
    # Clipped shadows (completely black)
    features['clipped_shadow_ratio'] = float(np.mean(gray <= 1))
    
    # Dynamic range
    features['dynamic_range'] = float(p99 - p1)
    features['contrast_ratio'] = float((p95 + 1) / (p5 + 1))

    # Highlight/shadow roll-off behavior (tone curve near extremes)
    features['highlight_rolloff_ratio'] = float((p99 - p95) / (p95 - p90 + 1e-10))
    features['shadow_rolloff_ratio'] = float((p10 - p5) / (p5 - p1 + 1e-10))
    
    # Tonal distribution (histogram shape)
    hist = np.bincount(gray.astype(np.uint8).ravel(), minlength=256)[:256].astype(np.float64)
    hist = hist / (hist.sum() + 1e-10)
    
    # Bimodality coefficient
    n = gray.size
    skewness = fast_skew(gray.ravel())
    kurtosis = fast_kurt(gray.ravel())
    features['bimodality_coefficient'] = float((skewness**2 + 1) / (kurtosis + 3 * (n-1)**2 / ((n-2)*(n-3)) + 1e-10))
    
    return features


def compute_skin_tone_features(
    img_array: np.ndarray,
    *,
    precomputed: Optional["ImagePrecomputedData"] = None,
) -> Dict[str, float]:
    """
    Estimate skin-tone manifold distance in YCbCr space.
    
    Uses a simple chroma range for skin pixels and computes
    distance to a canonical skin-tone center.
    """
    features = {
        'skin_ratio': 0.0,
        'skin_tone_dist_mean': 0.0,
        'skin_tone_dist_std': 0.0,
        'skin_tone_dist_p90': 0.0,
        'skin_tone_compactness': 0.0,
    }

    if img_array.ndim != 3 or img_array.shape[2] < 3:
        return features

    if precomputed is not None:
        r = precomputed.r
        g = precomputed.g
        b = precomputed.b
    else:
        img = img_array.astype(np.float64)
        r = img[:, :, 0]
        g = img[:, :, 1]
        b = img[:, :, 2]

    # Fast YCbCr conversion (BT.601)
    cb = 128.0 - 0.168736 * r - 0.331264 * g + 0.5 * b
    cr = 128.0 + 0.5 * r - 0.418688 * g - 0.081312 * b

    # Skin range in YCbCr
    skin_mask = (cb >= 77) & (cb <= 127) & (cr >= 133) & (cr <= 173)
    skin_ratio = float(np.mean(skin_mask))
    features['skin_ratio'] = skin_ratio

    if skin_ratio <= 0.0:
        return features

    cb_skin = cb[skin_mask]
    cr_skin = cr[skin_mask]
    center_cb, center_cr = 102.0, 153.0
    dist = np.sqrt((cb_skin - center_cb) ** 2 + (cr_skin - center_cr) ** 2)

    features['skin_tone_dist_mean'] = float(np.mean(dist))
    features['skin_tone_dist_std'] = float(np.std(dist))
    _flat_dist = dist.ravel()
    _idx90_d = int(0.90 * (len(_flat_dist) - 1))
    features['skin_tone_dist_p90'] = float(np.partition(_flat_dist, _idx90_d)[_idx90_d])
    features['skin_tone_compactness'] = float(1.0 / (features['skin_tone_dist_std'] + 1e-10))

    return features


def compute_contrast_curve_features(
    img_array: np.ndarray,
    *,
    precomputed: Optional["ImagePrecomputedData"] = None,
) -> Dict[str, float]:
    """
    Global contrast curve shape via grayscale CDF deviation.
    
    Unusual global tone curves can indicate synthetic rendering
    or aggressive post-processing.
    """
    features = {}

    if precomputed is not None:
        gray = precomputed.gray
        hist = precomputed.gray_histogram_raw
        _cumsum = precomputed.gray_histogram_cumsum
    else:
        gray = np.mean(img_array, axis=2).astype(np.float64)
        hist = np.bincount(gray.astype(np.uint8).ravel(), minlength=256)[:256]
        _cumsum = np.cumsum(hist)

    total = hist.sum()
    if total <= 0:
        features['contrast_cdf_deviation'] = 0.0
        features['contrast_cdf_curvature'] = 0.0
        features['contrast_curve_asym'] = 1.0
        features['contrast_cdf_slope_mid'] = 0.0
        return features

    cdf = np.cumsum(hist) / total
    ideal = np.linspace(1.0 / 256.0, 1.0, 256)
    features['contrast_cdf_deviation'] = float(np.mean(np.abs(cdf - ideal)))

    # Curvature proxy: second derivative magnitude
    if len(cdf) > 2:
        features['contrast_cdf_curvature'] = float(np.mean(np.abs(np.diff(cdf, 2))))
    else:
        features['contrast_cdf_curvature'] = 0.0

    _n_total = _cumsum[-1]
    p10 = float(np.searchsorted(_cumsum, _n_total * 0.10))
    p50 = float(np.searchsorted(_cumsum, _n_total * 0.50))
    p90 = float(np.searchsorted(_cumsum, _n_total * 0.90))
    features['contrast_curve_asym'] = float((p90 - p50) / (p50 - p10 + 1e-10))
    features['contrast_cdf_slope_mid'] = float((cdf[192] - cdf[64]) / 128.0)

    return features


def compute_local_contrast_features(
    img_array: np.ndarray,
    block_size: int = 32,
    *,
    precomputed: Optional["ImagePrecomputedData"] = None,
) -> Dict[str, float]:
    """
    Compute local contrast statistics.
    
    AI images may have inconsistent local contrast patterns.
    Uses reshape for fast block-based computation.
    """
    from scipy import ndimage
    
    if precomputed is not None:
        gray = precomputed.gray
    else:
        gray = np.mean(img_array, axis=2)
    h, w = gray.shape
    
    # Pad to make divisible by block_size
    pad_h = (block_size - h % block_size) % block_size
    pad_w = (block_size - w % block_size) % block_size
    gray_padded = np.pad(gray, ((0, pad_h), (0, pad_w)), mode='reflect')
    
    new_h, new_w = gray_padded.shape
    
    # Reshape into blocks
    blocks = gray_padded.reshape(new_h // block_size, block_size, new_w // block_size, block_size)
    blocks = blocks.transpose(0, 2, 1, 3).reshape(-1, block_size, block_size)
    
    # Compute Michelson contrast for each block - vectorized
    block_max = np.max(blocks, axis=(1, 2))
    block_min = np.min(blocks, axis=(1, 2))
    denominator = block_max + block_min
    local_contrasts = np.divide(block_max - block_min, denominator, out=np.zeros_like(denominator), where=denominator > 0)
    
    features = {
        'local_contrast_mean': float(np.mean(local_contrasts)),
        'local_contrast_std': float(np.std(local_contrasts)),
        'local_contrast_min': float(np.min(local_contrasts)),
        'local_contrast_max': float(np.max(local_contrasts)),
        'local_contrast_skew': float(fast_skew(local_contrasts)) if len(local_contrasts) > 2 else 0.0,
    }
    
    return features


def extract_color_features(
    img_array: np.ndarray,
    *,
    precomputed: Optional["ImagePrecomputedData"] = None,
) -> Dict[str, float]:
    """
    Extract all color-related features from an image.

    Args:
        img_array: RGB image as numpy array (H, W, 3), uint8
        precomputed: Optional precomputed image data for sharing across modules

    Returns:
        Dictionary of feature names to values
    """
    features = {}
    features.update(compute_rgb_features(img_array, precomputed=precomputed))
    features.update(_compute_hsv_features_from_array(precomputed.hsv))
    features.update(_compute_ycbcr_features_from_array(precomputed.ycbcr))
    features.update(_compute_lab_features_from_array(precomputed.lab))
    features.update(_compute_highlight_shadow_features_with_gray(img_array, precomputed.gray))
    features.update(compute_skin_tone_features(img_array, precomputed=precomputed))
    features.update(compute_contrast_curve_features(img_array, precomputed=precomputed))
    features.update(compute_local_contrast_features(img_array, precomputed=precomputed))
    return features


def _compute_hsv_features_from_array(hsv_array: np.ndarray) -> Dict[str, float]:
    """Compute HSV features from precomputed HSV array."""
    features = {}
    channel_names = ['h', 's', 'v']

    for i, name in enumerate(channel_names):
        moments = compute_histogram_moments(hsv_array[:, :, i])
        for key, value in moments.items():
            features[f'hsv_{name}_{key}'] = value

    # Saturation-specific features
    saturation = hsv_array[:, :, 1].ravel()
    features['hsv_high_sat_ratio'] = float(np.mean(saturation > 200))
    features['hsv_low_sat_ratio'] = float(np.mean(saturation < 30))
    features['hsv_mid_sat_ratio'] = float(np.mean((saturation >= 30) & (saturation <= 200)))

    sat_hist, _ = np.histogram(saturation, bins=32, range=(0, 256))
    sat_hist = sat_hist / (sat_hist.sum() + 1e-10)
    features['hsv_sat_entropy'] = fast_entropy_hist(sat_hist)

    sat_zero = float(np.sum(sat_hist == 0))
    features['hsv_sat_zero_bins'] = sat_zero
    features['hsv_sat_zero_ratio'] = float(sat_zero / 32.0)
    features['hsv_sat_hist_smoothness'] = float(np.mean(np.abs(np.diff(sat_hist))))
    features['hsv_sat_max_to_mean'] = float(np.max(sat_hist) / (np.mean(sat_hist) + 1e-10))

    # Hue distribution features
    hue = hsv_array[:, :, 0].ravel()
    hue_hist, _ = np.histogram(hue, bins=36, range=(0, 256))
    hue_hist = hue_hist / (hue_hist.sum() + 1e-10)
    features['hsv_hue_entropy'] = fast_entropy_hist(hue_hist)

    hue_zero = float(np.sum(hue_hist == 0))
    features['hsv_hue_zero_bins'] = hue_zero
    features['hsv_hue_zero_ratio'] = float(hue_zero / 36.0)
    features['hsv_hue_hist_smoothness'] = float(np.mean(np.abs(np.diff(hue_hist))))
    features['hsv_hue_max_to_mean'] = float(np.max(hue_hist) / (np.mean(hue_hist) + 1e-10))

    features['hsv_dominant_hue'] = float(np.argmax(hue_hist) * 10)
    features['hsv_hue_concentration'] = float(np.max(hue_hist))
    features['hsv_hist_gap_spike_score'] = float(
        (features['hsv_hue_max_to_mean'] + features['hsv_sat_max_to_mean']) / 2.0
    )

    return features


def _compute_ycbcr_features_from_array(ycbcr_array: np.ndarray) -> Dict[str, float]:
    """Compute YCbCr features from precomputed YCbCr array."""
    features = {}
    channel_names = ['y', 'cb', 'cr']

    for i, name in enumerate(channel_names):
        moments = compute_histogram_moments(ycbcr_array[:, :, i])
        for key, value in moments.items():
            features[f'ycbcr_{name}_{key}'] = value

    # Chroma-luma correlations
    y = ycbcr_array[:, :, 0].ravel()
    cb = ycbcr_array[:, :, 1].ravel()
    cr = ycbcr_array[:, :, 2].ravel()

    ycbcr_stack = np.vstack([y, cb, cr])
    corr_matrix = np.corrcoef(ycbcr_stack)
    features['ycbcr_corr_y_cb'] = float(corr_matrix[0, 1])
    features['ycbcr_corr_y_cr'] = float(corr_matrix[0, 2])
    features['ycbcr_corr_cb_cr'] = float(corr_matrix[1, 2])

    y_var = np.var(y)
    cb_var = np.var(cb)
    cr_var = np.var(cr)

    if y_var > 0:
        features['ycbcr_cb_y_var_ratio'] = float(cb_var / y_var)
        features['ycbcr_cr_y_var_ratio'] = float(cr_var / y_var)
    else:
        features['ycbcr_cb_y_var_ratio'] = 0.0
        features['ycbcr_cr_y_var_ratio'] = 0.0

    return features


def _compute_lab_features_from_array(lab: np.ndarray) -> Dict[str, float]:
    """Compute LAB features from precomputed LAB array."""
    features = {}
    channel_names = ['l', 'a', 'b']

    for i, name in enumerate(channel_names):
        channel = lab[:, :, i]
        features[f'lab_{name}_mean'] = float(np.mean(channel))
        features[f'lab_{name}_std'] = float(np.std(channel))
        features[f'lab_{name}_skew'] = float(fast_skew(channel.ravel()))
        features[f'lab_{name}_kurt'] = float(fast_kurt(channel.ravel()))

    # Color gamut usage
    a_channel = lab[:, :, 1].ravel()
    b_channel = lab[:, :, 2].ravel()

    chroma = np.sqrt(a_channel**2 + b_channel**2)
    features['lab_chroma_mean'] = float(np.mean(chroma))
    features['lab_chroma_std'] = float(np.std(chroma))
    features['lab_chroma_max'] = float(np.max(chroma))

    hue_angle = np.arctan2(b_channel, a_channel + 1e-10)
    mean_sin = np.mean(np.sin(hue_angle))
    mean_cos = np.mean(np.cos(hue_angle))
    features['lab_hue_circular_mean'] = float(np.arctan2(mean_sin, mean_cos))

    r_value = np.sqrt(mean_cos**2 + mean_sin**2)
    r_value = np.clip(r_value, 1e-10, 1.0)
    circ_std = np.sqrt(np.abs(-2 * np.log(r_value)))
    if np.isnan(circ_std) or np.isinf(circ_std):
        circ_std = 0.0
    features['lab_hue_circular_std'] = float(circ_std)

    return features


def _compute_highlight_shadow_features_with_gray(
    img_array: np.ndarray, gray: np.ndarray
) -> Dict[str, float]:
    """Compute highlight/shadow features using precomputed grayscale."""
    features = {}

    # Fast histogram-based percentiles (gray values are in [0, 255])
    gray_u8 = gray.astype(np.uint8) if gray.dtype != np.uint8 else gray
    hist_raw = np.bincount(gray_u8.ravel(), minlength=256)
    cumsum = np.cumsum(hist_raw)
    n = cumsum[-1]
    p1 = float(np.searchsorted(cumsum, n * 0.01))
    p5 = float(np.searchsorted(cumsum, n * 0.05))
    p10 = float(np.searchsorted(cumsum, n * 0.10))
    p90 = float(np.searchsorted(cumsum, n * 0.90))
    p95 = float(np.searchsorted(cumsum, n * 0.95))
    p99 = float(np.searchsorted(cumsum, n * 0.99))

    highlight_threshold = p95
    highlights = gray[gray >= highlight_threshold]

    features['highlight_ratio'] = float(len(highlights) / gray.size)
    features['highlight_mean'] = float(np.mean(highlights)) if len(highlights) > 0 else 0.0
    features['highlight_std'] = float(np.std(highlights)) if len(highlights) > 0 else 0.0
    features['clipped_highlight_ratio'] = float(np.mean(gray >= 254))

    shadow_threshold = p5
    shadows = gray[gray <= shadow_threshold]

    features['shadow_ratio'] = float(len(shadows) / gray.size)
    features['shadow_mean'] = float(np.mean(shadows)) if len(shadows) > 0 else 0.0
    features['shadow_std'] = float(np.std(shadows)) if len(shadows) > 0 else 0.0
    features['clipped_shadow_ratio'] = float(np.mean(gray <= 1))

    features['dynamic_range'] = float(p99 - p1)
    features['contrast_ratio'] = float((p95 + 1) / (p5 + 1))

    features['highlight_rolloff_ratio'] = float((p99 - p95) / (p95 - p90 + 1e-10))
    features['shadow_rolloff_ratio'] = float((p10 - p5) / (p5 - p1 + 1e-10))

    hist = np.bincount(gray.astype(np.uint8).ravel(), minlength=256)[:256].astype(np.float64)
    hist = hist / (hist.sum() + 1e-10)

    n = gray.size
    skewness = fast_skew(gray.ravel())
    kurtosis = fast_kurt(gray.ravel())
    features['bimodality_coefficient'] = float((skewness**2 + 1) / (kurtosis + 3 * (n-1)**2 / ((n-2)*(n-3)) + 1e-10))

    return features
