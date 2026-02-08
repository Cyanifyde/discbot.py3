"""
features_model_specific.py - Model-Specific Detection Features for AI detection.

Implements detection of fingerprints specific to different AI generation models:
- GAN-specific artifacts (checkerboard, mode collapse patterns)
- Diffusion model artifacts (denoising patterns, guidance artifacts)
- Stylistic artifacts (overly cinematic, perfect lighting)
- Cross-model generalization features

Different AI models (Midjourney, Stable Diffusion, DALL-E, GANs) leave
distinct signatures that can be detected.
"""

import numpy as np
from scipy import ndimage, signal, fftpack
from typing import Dict, List, Tuple, Optional, TYPE_CHECKING
import warnings
import os
import threading

try:
    from gpu_utils import gpu_uniform_filter, gpu_local_variance, gpu_local_contrast
    _HAS_GPU_UTILS = True
except ImportError:
    _HAS_GPU_UTILS = False

if TYPE_CHECKING:
    from image_precomputed import ImagePrecomputedData

# OpenCV for faster operations
try:
    import cv2
    HAS_CV2 = True
except ImportError:
    HAS_CV2 = False

# Try to import JIT utilities for acceleration
try:
    from jit_utils import fast_moments, fast_sobel_magnitude, fast_corrcoef, fast_glcm_features, fast_local_std_2d, fast_rgb_to_saturation
    HAS_JIT = True
except ImportError:
    HAS_JIT = False

from utils import fast_skew, fast_kurt, fast_entropy_hist

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


def _fast_corrcoef(a: np.ndarray, b: np.ndarray) -> float:
    """Fast correlation between two 1D arrays."""
    a_arr = np.asarray(a, dtype=np.float64).ravel()
    b_arr = np.asarray(b, dtype=np.float64).ravel()
    if a_arr.size < 2 or b_arr.size < 2:
        return 0.0
    if HAS_JIT:
        return fast_corrcoef(a_arr, b_arr)
    a = np.asarray(a, dtype=np.float64).ravel()
    b = np.asarray(b, dtype=np.float64).ravel()
    if a.size < 2 or b.size < 2:
        return 0.0
    a_mean = np.mean(a)
    b_mean = np.mean(b)
    a_centered = a - a_mean
    b_centered = b - b_mean
    denom = np.sqrt(np.dot(a_centered, a_centered) * np.dot(b_centered, b_centered)) + 1e-10
    return float(np.dot(a_centered, b_centered) / denom)


_CLIP_LOCK = threading.Lock()
_CLIP_MODEL = None
_CLIP_PREPROCESS = None
_CLIP_TEXT_EMB = None


def _clip_enabled() -> bool:
    val = os.environ.get("AI_DETECTOR_ENABLE_CLIP", "").strip().lower()
    return val in ("1", "true", "yes", "y", "on")


def _get_clip_assets():
    """
    Lazily load CLIP model + preprocess + cached text embeddings.

    This is optional and only used when AI_DETECTOR_ENABLE_CLIP=1 and
    dependencies are installed. Otherwise, callers should skip.
    """
    global _CLIP_MODEL, _CLIP_PREPROCESS, _CLIP_TEXT_EMB

    with _CLIP_LOCK:
        if _CLIP_MODEL is not None and _CLIP_PREPROCESS is not None and _CLIP_TEXT_EMB is not None:
            return _CLIP_MODEL, _CLIP_PREPROCESS, _CLIP_TEXT_EMB

        import torch  # type: ignore
        import open_clip  # type: ignore

        device = "cuda" if torch.cuda.is_available() else "cpu"
        model_name = os.environ.get("AI_DETECTOR_CLIP_MODEL", "ViT-B-32")
        pretrained = os.environ.get("AI_DETECTOR_CLIP_PRETRAINED", "openai")

        model, _, preprocess = open_clip.create_model_and_transforms(model_name, pretrained=pretrained, device=device)
        model.eval()

        tokenizer = open_clip.get_tokenizer(model_name)
        prompts = [
            "masterpiece",
            "best quality",
            "highly detailed",
            "award-winning illustration",
            "trending on artstation",
        ]
        with torch.no_grad():
            text = tokenizer(prompts).to(device)
            text_emb = model.encode_text(text)
            text_emb = text_emb / (text_emb.norm(dim=-1, keepdim=True) + 1e-10)

        _CLIP_MODEL = model
        _CLIP_PREPROCESS = preprocess
        _CLIP_TEXT_EMB = text_emb
        return _CLIP_MODEL, _CLIP_PREPROCESS, _CLIP_TEXT_EMB


def _compute_clip_aesthetic_features(img_array: np.ndarray) -> Dict[str, float]:
    """Optional CLIP-based 'masterpiece' similarity features."""
    if not _clip_enabled():
        return {}

    try:
        model, preprocess, text_emb = _get_clip_assets()
        import torch  # type: ignore
        from PIL import Image  # local import to avoid dependency when CLIP is off
    except Exception:
        return {}

    try:
        device = next(model.parameters()).device
        img = Image.fromarray(img_array.astype(np.uint8))
        with torch.no_grad():
            inp = preprocess(img).unsqueeze(0).to(device)
            img_emb = model.encode_image(inp)
            img_emb = img_emb / (img_emb.norm(dim=-1, keepdim=True) + 1e-10)
            sims = (img_emb @ text_emb.T).squeeze(0).detach().cpu().numpy()
        sims = np.asarray(sims, dtype=np.float64)
        return {
            "clip_masterpiece_sim_max": float(np.max(sims)) if sims.size else 0.0,
            "clip_masterpiece_sim_mean": float(np.mean(sims)) if sims.size else 0.0,
        }
    except Exception:
        return {}


def compute_gan_fingerprint_features(
    img_array: np.ndarray,
    *,
    precomputed: Optional["ImagePrecomputedData"] = None,
) -> Dict[str, float]:
    """
    Detect GAN-specific artifacts.
    """
    features = {}
    gray = precomputed.gray
    log_magnitude = precomputed.fft_log_magnitude
    high_pass = precomputed.high_pass

    h, w = gray.shape
    cy, cx = h // 2, w // 2
    
    # 1. Checkerboard artifact detection
    checkerboard = np.indices((h, w)).sum(axis=0) % 2
    checkerboard = checkerboard.astype(np.float32) * 2 - 1
    
    hp_flat = high_pass.ravel()
    cb_flat = checkerboard.ravel()
    
    checkerboard_corr = np.abs(_fast_corrcoef(hp_flat, cb_flat))
    features['gan_checkerboard_corr'] = float(checkerboard_corr)
    
    for block_size in [2, 4]:
        cb_block = (np.indices((h, w)).sum(axis=0) // block_size) % 2
        cb_block = cb_block.astype(np.float32) * 2 - 1
        corr = np.abs(_fast_corrcoef(hp_flat, cb_block.ravel()))
        features[f'gan_checkerboard_{block_size}x_corr'] = float(corr)
    
    # 2. Frequency domain checkerboard detection
    corner_size = min(10, h // 10, w // 10)
    if corner_size > 0:
        corners = [
            log_magnitude[:corner_size, :corner_size],
            log_magnitude[:corner_size, -corner_size:],
            log_magnitude[-corner_size:, :corner_size],
            log_magnitude[-corner_size:, -corner_size:],
        ]
        corner_energy = np.sum([np.sum(c ** 2) for c in corners])
        total_energy = np.sum(log_magnitude ** 2) + 1e-10
        features['gan_corner_freq_ratio'] = float(corner_energy / total_energy)
    else:
        features['gan_corner_freq_ratio'] = 0.0
    
    # 3. Mode collapse / texture repetition detection
    center_crop = gray[h//4:3*h//4, w//4:3*w//4]
    ch, cw = center_crop.shape
    
    if ch > 32 and cw > 32:
        centered = center_crop - np.mean(center_crop)
        fft_centered = _fast_fft2(centered)
        auto_corr = _fast_ifft2(fft_centered * np.conj(fft_centered)).real
        auto_corr = _fast_fftshift(auto_corr)
        auto_corr = auto_corr / (auto_corr.max() + 1e-10)
        
        c_cy, c_cx = ch // 2, cw // 2
        mask_size = min(20, ch // 10, cw // 10)
        auto_corr_masked = auto_corr.copy()
        auto_corr_masked[c_cy-mask_size:c_cy+mask_size, c_cx-mask_size:c_cx+mask_size] = 0
        
        high_corr = auto_corr_masked > 0.5
        features['gan_repetition_peaks'] = float(np.sum(high_corr))
        features['gan_max_secondary_corr'] = float(np.max(auto_corr_masked))
    else:
        features['gan_repetition_peaks'] = 0.0
        features['gan_max_secondary_corr'] = 0.0
    
    # 4. GAN noise pattern analysis
    noise_magnitude = precomputed.fft_magnitude # Approximate, but shared
    # Use high_pass for noise residual analysis if needed
    noise_residual = precomputed.noise_residual
    
    y, x = np.ogrid[:h, :w]
    r = np.sqrt((x - cx) ** 2 + (y - cy) ** 2).astype(int)
    max_r = min(cy, cx)
    
    r_flat = r.ravel()
    noise_flat = noise_magnitude.ravel()
    valid_mask = r_flat < max_r
    r_valid = r_flat[valid_mask]
    noise_valid = noise_flat[valid_mask]
    
    radial_sum = np.bincount(r_valid, weights=noise_valid, minlength=max_r)
    radial_count = np.bincount(r_valid, minlength=max_r)
    radial_profile = np.divide(radial_sum, radial_count, out=np.zeros(max_r), where=radial_count > 0)
    
    if max_r > 10:
        low_freq = np.mean(radial_profile[:max_r//4])
        high_freq = np.mean(radial_profile[3*max_r//4:])
        features['gan_noise_hf_lf_ratio'] = float(high_freq / (low_freq + 1e-10))
        profile_slope = np.polyfit(np.arange(len(radial_profile)), radial_profile, 1)[0]
        features['gan_noise_radial_slope'] = float(profile_slope)
    else:
        features['gan_noise_hf_lf_ratio'] = 0.0
        features['gan_noise_radial_slope'] = 0.0
    
    return features


def compute_diffusion_fingerprint_features(
    img_array: np.ndarray,
    *,
    precomputed: Optional["ImagePrecomputedData"] = None,
) -> Dict[str, float]:
    """
    Detect diffusion model (Stable Diffusion, Midjourney, DALL-E) specific artifacts.
    """
    features = {}
    gray = precomputed.gray
    h, w = gray.shape
    
    # 1. Denoising pattern detection
    scales = [1, 2, 4]
    denoising_errors = []
    
    # Sigma 1 and 2 are usually in precomputed
    sigma_vals = {1: precomputed.gaussian_1, 2: precomputed.gaussian_2, 4: None}
    
    for sigma in scales:
        denoised = sigma_vals[sigma]
        if denoised is None:
            denoised = precomputed.gaussian(float(sigma))
        residual = gray - denoised
        features[f'diffusion_residual_std_s{sigma}'] = float(np.std(residual))
        features[f'diffusion_residual_kurtosis_s{sigma}'] = float(fast_kurt(residual.ravel()))
        denoising_errors.append(np.std(residual))
    
    features['diffusion_scale_ratio_1_2'] = float(denoising_errors[0] / (denoising_errors[1] + 1e-10))
    features['diffusion_scale_ratio_2_4'] = float(denoising_errors[1] / (denoising_errors[2] + 1e-10))
    
    # 2. Texture smoothness with detail
    local_var = precomputed.local_variance
    
    var_hist, _ = np.histogram(local_var.ravel(), bins=50)
    var_hist = var_hist / (var_hist.sum() + 1e-10)
    features['diffusion_var_entropy'] = fast_entropy_hist(var_hist)
    
    peaks = []
    for i in range(1, len(var_hist) - 1):
        if var_hist[i] > var_hist[i-1] and var_hist[i] > var_hist[i+1]:
            peaks.append((i, var_hist[i]))
    
    features['diffusion_var_num_peaks'] = float(len(peaks))
    if len(peaks) >= 2:
        peaks_sorted = sorted(peaks, key=lambda x: x[1], reverse=True)[:2]
        peak_distance = abs(peaks_sorted[0][0] - peaks_sorted[1][0])
        features['diffusion_var_peak_separation'] = float(peak_distance)
    else:
        features['diffusion_var_peak_separation'] = 0.0
    
    # 3. Color distribution analysis
    for name in ['r', 'g', 'b']:
        hist = precomputed.channel_histogram(name)
        hist_gradient = np.abs(np.diff(hist))
        features[f'diffusion_{name}_hist_smoothness'] = float(np.mean(hist_gradient))
        features[f'diffusion_{name}_hist_entropy'] = fast_entropy_hist(hist)
    
    # 4. Edge quality analysis
    edge_magnitude = precomputed.edges
    
    edge_hist, _ = np.histogram(edge_magnitude.ravel(), bins=50)
    edge_hist = edge_hist / (edge_hist.sum() + 1e-10)
    features['diffusion_edge_entropy'] = fast_entropy_hist(edge_hist)
    
    # Use partition for O(n) percentile
    edge_flat = edge_magnitude.ravel()
    p95_idx = min(len(edge_flat) - 1, int(0.95 * len(edge_flat)))
    p95_val = np.partition(edge_flat, p95_idx)[p95_idx]
    strong_edges = edge_magnitude > p95_val
    if np.sum(strong_edges) > 0:
        strong_edge_values = edge_magnitude[strong_edges]
        features['diffusion_strong_edge_mean'] = float(np.mean(strong_edge_values))
        features['diffusion_strong_edge_std'] = float(np.std(strong_edge_values))
    else:
        features['diffusion_strong_edge_mean'] = 0.0
        features['diffusion_strong_edge_std'] = 0.0
    
    # 5. Guidance artifact detection - use cv2.dilate/erode (35x faster)
    if HAS_CV2:
        kernel = np.ones((5, 5), np.uint8)
        gray_uint8 = (gray * 255 / (gray.max() + 1e-10)).astype(np.uint8)
        contrast = cv2.dilate(gray_uint8, kernel).astype(np.float32) - cv2.erode(gray_uint8, kernel).astype(np.float32)
    else:
        contrast = ndimage.maximum_filter(gray, size=5) - ndimage.minimum_filter(gray, size=5)
    features['diffusion_contrast_mean'] = float(np.mean(contrast))
    features['diffusion_contrast_std'] = float(np.std(contrast))
    # Use partition for O(n) percentile
    contrast_flat = contrast.ravel()
    p90_idx = min(len(contrast_flat) - 1, int(0.90 * len(contrast_flat)))
    p90_val = np.partition(contrast_flat, p90_idx)[p90_idx]
    high_contrast_mask = contrast > p90_val
    features['diffusion_high_contrast_ratio'] = float(np.mean(high_contrast_mask))
    
    return features


def compute_stylistic_artifact_features(
    img_array: np.ndarray,
    *,
    precomputed: Optional["ImagePrecomputedData"] = None,
) -> Dict[str, float]:
    """
    Detect stylistic artifacts common in AI-generated images.
    """
    features = {}
    h, w = img_array.shape[:2]
    
    r, g, b = precomputed.r, precomputed.g, precomputed.b
    gray = precomputed.gray
    laplacian = precomputed.laplacian
    
    # 1. Color grading analysis
    mean_r, mean_g, mean_b = np.mean(r), np.mean(g), np.mean(b)
    features['style_color_cast_r'] = float(mean_r - (mean_r + mean_g + mean_b) / 3)
    features['style_color_cast_g'] = float(mean_g - (mean_r + mean_g + mean_b) / 3)
    features['style_color_cast_b'] = float(mean_b - (mean_r + mean_g + mean_b) / 3)
    
    _p30, _p70 = precomputed.gray_percentiles([30, 70])
    shadow_mask = gray < _p30
    highlight_mask = gray > _p70
    
    if np.sum(shadow_mask) > 100 and np.sum(highlight_mask) > 100:
        shadow_r = np.mean(r[shadow_mask])
        shadow_b = np.mean(b[shadow_mask])
        highlight_r = np.mean(r[highlight_mask])
        highlight_b = np.mean(b[highlight_mask])
        features['style_teal_shadows'] = float(shadow_b - shadow_r)
        features['style_orange_highlights'] = float(highlight_r - highlight_b)
        features['style_teal_orange_split'] = float(features['style_orange_highlights'] - features['style_teal_shadows'])
    else:
        features['style_teal_shadows'] = 0.0
        features['style_orange_highlights'] = 0.0
        features['style_teal_orange_split'] = 0.0
    
    # 2. Saturation analysis
    saturation = precomputed.hsv[:, :, 1]
    
    features['style_saturation_mean'] = float(np.mean(saturation))
    features['style_saturation_std'] = float(np.std(saturation))
    sat_hist, _ = np.histogram(saturation.ravel(), bins=50, range=(0, 256))
    sat_hist = sat_hist / (sat_hist.sum() + 1e-10)
    features['style_saturation_entropy'] = fast_entropy_hist(sat_hist)
    features['style_high_saturation_ratio'] = float(np.mean(saturation > 200))
    
    # 3. Dynamic range analysis
    features['style_dynamic_range'] = float(np.max(gray) - np.min(gray))
    _p5, _p95 = precomputed.gray_percentiles([5, 95])
    features['style_shadow_level'] = float(_p5)
    features['style_highlight_level'] = float(_p95)
    features['style_contrast_ratio'] = float(features['style_highlight_level'] / (features['style_shadow_level'] + 1))
    
    # 4. Sharpness perfection
    features['style_sharpness_mean'] = float(np.mean(np.abs(laplacian)))
    features['style_sharpness_max'] = float(np.max(np.abs(laplacian)))
    features['style_sharpness_var'] = float(np.var(laplacian))
    
    if _HAS_GPU_UTILS:
        local_sharpness = gpu_uniform_filter(np.abs(laplacian), size=20)
    elif HAS_CV2:
        local_sharpness = cv2.blur(np.abs(laplacian).astype(np.float32), (20, 20), borderType=cv2.BORDER_REFLECT)
    else:
        local_sharpness = ndimage.uniform_filter(np.abs(laplacian), size=20)
    features['style_sharpness_uniformity'] = float(np.std(local_sharpness) / (np.mean(local_sharpness) + 1e-10))
    
    # 5. Vignette and lighting analysis
    center_y, center_x = h // 2, w // 2
    y, x = np.ogrid[:h, :w]
    dist_from_center = np.sqrt((y - center_y) ** 2 + (x - center_x) ** 2)
    vignette_corr = _fast_corrcoef(dist_from_center.ravel(), gray.ravel())
    features['style_vignette_strength'] = float(-vignette_corr)
    
    # 6. Aesthetic uniformity
    region_h, region_w = h // 3, w // 3
    region_aesthetics = []
    for i in range(3):
        for j in range(3):
            region = gray[i*region_h:(i+1)*region_h, j*region_w:(j+1)*region_w]
            region_aesthetics.append(np.std(region))
    features['style_aesthetic_uniformity'] = float(np.std(region_aesthetics) / (np.mean(region_aesthetics) + 1e-10))

    edge_magnitude = precomputed.edges
    
    _flat_em = edge_magnitude.ravel()
    _idx85 = int(0.85 * (len(_flat_em) - 1))
    edge_thr = np.partition(_flat_em, _idx85)[_idx85]
    edge_density = float(np.mean(edge_magnitude > edge_thr))
    features["style_edge_density"] = edge_density

    # We don't have entropy in precomputed but we have histogram
    hist = precomputed.gray_histogram
    entropy = fast_entropy_hist(hist)
    
    complexity = float((edge_density + (entropy / 8.0)) / 2.0)
    features["style_gray_entropy"] = entropy
    features["style_complexity_proxy"] = complexity

    aesthetic_proxy = float(
        (features.get("style_high_saturation_ratio", 0.0) + max(0.0, features.get("style_vignette_strength", 0.0))) / 2.0
        + min(1.0, abs(features.get("style_teal_orange_split", 0.0)) / 80.0)
    )
    features["style_aesthetic_proxy"] = aesthetic_proxy
    features["style_aesthetic_mismatch_proxy"] = float(aesthetic_proxy / (complexity + 1e-10))
    features["style_aesthetic_mismatch_delta"] = float(aesthetic_proxy - complexity)

    clip_feats = _compute_clip_aesthetic_features(img_array)
    features.update(clip_feats)
    if clip_feats:
        features["clip_masterpiece_mismatch"] = float(clip_feats.get("clip_masterpiece_sim_max", 0.0) - complexity)
    
    return features


def compute_cross_model_features(
    img_array: np.ndarray,
    *,
    precomputed: Optional["ImagePrecomputedData"] = None,
) -> Dict[str, float]:
    """
    Compute features for cross-model generalization.
    """
    features = {}
    gray = precomputed.gray
    magnitude = precomputed.fft_magnitude
    
    h, w = gray.shape
    
    # 1. Naturalness statistics
    grad_x = np.diff(gray, axis=1)
    grad_y = np.diff(gray, axis=0)
    grad_combined = np.concatenate([grad_x.ravel(), grad_y.ravel()])
    if HAS_JIT:
        _, _, grad_skew, grad_kurt = fast_moments(grad_combined.astype(np.float32))
        features['cross_gradient_kurtosis'] = float(grad_kurt)
        features['cross_gradient_skewness'] = float(grad_skew)
    else:
        features['cross_gradient_kurtosis'] = float(fast_kurt(grad_combined))
        features['cross_gradient_skewness'] = float(fast_skew(grad_combined))
    
    # 2. Spectral naturalness
    cy, cx = h // 2, w // 2
    y, x = np.ogrid[:h, :w]
    r = np.sqrt((x - cx) ** 2 + (y - cy) ** 2) + 1
    log_r = np.log(r.ravel())
    log_mag = np.log(magnitude.ravel() + 1)
    slope = np.polyfit(log_r[:10000], log_mag[:10000], 1)[0]
    features['cross_spectral_slope'] = float(slope)
    features['cross_spectral_deviation'] = float(abs(slope + 1))
    
    # 3. Local entropy analysis
    block_size = 16
    gray_int = (gray / 8).astype(np.int32)
    gray_int = np.clip(gray_int, 0, 31)
    
    h_blocks = max(1, (h - block_size) // block_size)
    w_blocks = max(1, (w - block_size) // block_size)
    step_h = max(1, h_blocks // 8)
    step_w = max(1, w_blocks // 8)
    max_blocks = ((h_blocks // step_h) + 1) * ((w_blocks // step_w) + 1)
    local_entropies = np.zeros(max_blocks)
    idx = 0
    
    for bi in range(0, h_blocks, step_h):
        for bj in range(0, w_blocks, step_w):
            i, j = bi * block_size, bj * block_size
            if i + block_size <= h and j + block_size <= w:
                block = gray_int[i:i+block_size, j:j+block_size].ravel()
                hist = np.bincount(block, minlength=32).astype(np.float64)
                hist = hist / (hist.sum() + 1e-10)
                local_entropies[idx] = fast_entropy_hist(hist)
                idx += 1
    
    if idx > 0:
        local_entropies = local_entropies[:idx]
        features['cross_local_entropy_mean'] = float(np.mean(local_entropies))
        features['cross_local_entropy_std'] = float(np.std(local_entropies))
        features['cross_local_entropy_cv'] = float(np.std(local_entropies) / (np.mean(local_entropies) + 1e-10))
    else:
        features['cross_local_entropy_mean'] = 0.0
        features['cross_local_entropy_std'] = 0.0
        features['cross_local_entropy_cv'] = 0.0
    
    # 4. Texture regularity (GLCM)
    gray_uint8 = precomputed.gray_uint8
    if HAS_JIT:
        # Use 16 levels for speed
        gray_q = (gray_uint8 // 16).astype(np.int32)
        contrast, homogeneity, energy, correlation = fast_glcm_features(gray_q, levels=16)
        features['cross_glcm_contrast'] = float(contrast)
        features['cross_glcm_homogeneity'] = float(homogeneity)
        # entropy is not returned by JIT but we can approximate or use non-JIT
    
    # Non-JIT fallback or additional features
    step = 1
    sample_size = min(100000, (h - step) * (w - step))
    indices = np.random.choice((h - step) * (w - step), sample_size, replace=False)
    i_coords = indices // (w - step)
    j_coords = indices % (w - step)
    val1 = gray_uint8[i_coords, j_coords]
    val2 = gray_uint8[i_coords, j_coords + step]
    linear_idx = val1.astype(np.int32) * 256 + val2.astype(np.int32)
    co_occurrence = np.bincount(linear_idx, minlength=65536).reshape(256, 256).astype(np.float64)
    co_occurrence = co_occurrence / (co_occurrence.sum() + 1e-10)
    
    if not HAS_JIT:
        # Precompute index arrays
        idx = np.arange(256)
        diff_sq = (idx[:, None] - idx[None, :])**2
        abs_diff = np.abs(idx[:, None] - idx[None, :])
        features['cross_glcm_contrast'] = float(np.sum(co_occurrence * diff_sq))
        features['cross_glcm_homogeneity'] = float(np.sum(co_occurrence / (1 + abs_diff + 1e-10)))
    
    features['cross_glcm_entropy'] = fast_entropy_hist(co_occurrence)
    
    # 5. Edge co-occurrence
    sobel_mag = precomputed.edges
    
    _flat_sm = sobel_mag.ravel()
    _idx80 = int(0.80 * (len(_flat_sm) - 1))
    edge_binary = (sobel_mag > np.partition(_flat_sm, _idx80)[_idx80]).astype(np.float32)
    
    # Vectorized block analysis
    n_blocks_h = (h - 16) // 16
    n_blocks_w = (w - 16) // 16
    if n_blocks_h > 0 and n_blocks_w > 0:
        # Reshape into blocks and compute mean per block
        cropped = edge_binary[:n_blocks_h*16, :n_blocks_w*16]
        blocks = cropped.reshape(n_blocks_h, 16, n_blocks_w, 16)
        edge_density_blocks = blocks.mean(axis=(1, 3))
        features['cross_edge_density_var'] = float(np.var(edge_density_blocks))
        features['cross_edge_density_cv'] = float(np.std(edge_density_blocks) / (np.mean(edge_density_blocks) + 1e-10))
    else:
        features['cross_edge_density_var'] = 0.0
        features['cross_edge_density_cv'] = 0.0
    
    # 6. Color naturalness
    r_f, g_f, b_f = precomputed.r.ravel(), precomputed.g.ravel(), precomputed.b.ravel()
    
    features['cross_color_corr_rg'] = float(_fast_corrcoef(r_f, g_f))
    features['cross_color_corr_rb'] = float(_fast_corrcoef(r_f, b_f))
    features['cross_color_corr_gb'] = float(_fast_corrcoef(g_f, b_f))
    features['cross_color_corr_avg'] = (features['cross_color_corr_rg'] + features['cross_color_corr_rb'] + features['cross_color_corr_gb']) / 3
    
    return features


def extract_model_specific_features(
    img_array: np.ndarray,
    *,
    precomputed: Optional["ImagePrecomputedData"] = None,
) -> Dict[str, float]:
    """
    Extract all model-specific detection features.
    """
    features = {}
    
    features.update(compute_gan_fingerprint_features(img_array, precomputed=precomputed))
    features.update(compute_diffusion_fingerprint_features(img_array, precomputed=precomputed))
    features.update(compute_stylistic_artifact_features(img_array, precomputed=precomputed))
    features.update(compute_cross_model_features(img_array, precomputed=precomputed))
    
    return features
