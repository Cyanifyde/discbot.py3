"""
detect.py - Simple AI Image Detection for Discord

Usage:
    python detect.py <image_path>
    python detect.py image.png
    python detect.py https://example.com/image.jpg

Output:
    Returns AI probability as a scalar value between 0.0 and 1.0
    - 0.0 = definitely real
    - 1.0 = definitely AI-generated
"""

import sys
import joblib
import numpy as np
from pathlib import Path
from PIL import Image
import warnings

# Suppress warnings for cleaner output
warnings.filterwarnings('ignore')

# Feature families used by this model
SELECTED_FAMILIES = [
    'color',
    'frequency',
    'spectral_diffusion',
    'noise',
    'texture',
    'gradient',
    'forensic',
    'model_specific',
    'nss',
    'cfa',
    'self_similarity',
    'residual',
]

MAX_DIMENSION = 512  # Model was trained with 512 max dimension


def load_model():
    """Load the trained model."""
    model_path = Path(__file__).parent / 'best.pkl'
    if not model_path.exists():
        print(f"ERROR: Model file not found: {model_path}", file=sys.stderr)
        sys.exit(1)
    
    return joblib.load(model_path)


def load_image(image_path):
    """Load image from file path or URL."""
    if image_path.startswith('http://') or image_path.startswith('https://'):
        # Download from URL
        import urllib.request
        import io
        
        print(f"Downloading image from URL...", file=sys.stderr)
        with urllib.request.urlopen(image_path) as response:
            image_data = response.read()
        image = Image.open(io.BytesIO(image_data))
    else:
        # Load from file
        image_path = Path(image_path)
        if not image_path.exists():
            print(f"ERROR: Image file not found: {image_path}", file=sys.stderr)
            sys.exit(1)
        image = Image.open(image_path)
    
    # Convert to RGB
    if image.mode != 'RGB':
        image = image.convert('RGB')
    
    return image


def extract_features(image):
    """Extract features from image."""
    from features import FeatureExtractor
    
    extractor = FeatureExtractor(
        families=SELECTED_FAMILIES,
        resize_for_speed=True,
        max_dimension=MAX_DIMENSION,
        n_jobs=1,
        use_cache=False,
        use_gpu=False,
    )
    
    return extractor.extract(image)


def predict(image_path, verbose=False):
    """
    Predict if image is AI-generated.
    
    Args:
        image_path: Path to image file or URL
        verbose: If True, print detailed information
    
    Returns:
        float: AI probability (0.0 to 1.0)
    """
    # Load model
    if verbose:
        print("Loading model...", file=sys.stderr)
    model_bundle = load_model()
    model = model_bundle['model']
    scaler = model_bundle['scaler']
    feature_names = model_bundle['feature_names']
    
    # Load image
    if verbose:
        print(f"Loading image: {image_path}", file=sys.stderr)
    image = load_image(image_path)
    if verbose:
        print(f"  Size: {image.size}", file=sys.stderr)
    
    # Extract features
    if verbose:
        print("Extracting features...", file=sys.stderr)
    features = extract_features(image)
    
    # Build feature vector
    vec = np.zeros(len(feature_names), dtype=np.float32)
    for i, name in enumerate(feature_names):
        vec[i] = features.get(name, 0.0)
    
    # Clean NaN/inf
    vec = np.nan_to_num(vec, nan=0.0, posinf=0.0, neginf=0.0)
    X = vec.reshape(1, -1)
    
    # Scale and predict
    X_sc = scaler.transform(X)
    proba = model.predict_proba(X_sc)[0]
    
    # Return AI probability (class 1)
    ai_probability = float(proba[1])
    
    return ai_probability


def predict_image(image, verbose=False):
    """
    Predict if image is AI-generated from a PIL Image object.
    
    Args:
        image: PIL Image object (already loaded)
        verbose: If True, print detailed information
    
    Returns:
        float: AI probability (0.0 to 1.0)
    """
    # Load model
    if verbose:
        print("Loading model...", file=sys.stderr)
    model_bundle = load_model()
    model = model_bundle['model']
    scaler = model_bundle['scaler']
    feature_names = model_bundle['feature_names']
    
    # Image is already loaded, just ensure RGB
    if verbose:
        print(f"Image size: {image.size}", file=sys.stderr)
    
    if image.mode != 'RGB':
        image = image.convert('RGB')
    
    # Extract features
    if verbose:
        print("Extracting features...", file=sys.stderr)
    features = extract_features(image)
    
    # Build feature vector
    vec = np.zeros(len(feature_names), dtype=np.float32)
    for i, name in enumerate(feature_names):
        vec[i] = features.get(name, 0.0)
    
    # Clean NaN/inf
    vec = np.nan_to_num(vec, nan=0.0, posinf=0.0, neginf=0.0)
    X = vec.reshape(1, -1)
    
    # Scale and predict
    X_sc = scaler.transform(X)
    proba = model.predict_proba(X_sc)[0]
    
    # Return AI probability (class 1)
    ai_probability = float(proba[1])
    
    return ai_probability


def main():
    """Main entry point."""
    if len(sys.argv) < 2:
        print("Usage: python detect.py <image_path>", file=sys.stderr)
        print("       python detect.py image.png", file=sys.stderr)
        print("       python detect.py https://example.com/image.jpg", file=sys.stderr)
        sys.exit(1)
    
    image_path = sys.argv[1]
    verbose = '--verbose' in sys.argv or '-v' in sys.argv
    
    try:
        ai_prob = predict(image_path, verbose=verbose)
        
        if verbose:
            print(f"\nResult:", file=sys.stderr)
            print(f"  AI Probability: {ai_prob:.6f}", file=sys.stderr)
            print(f"  Real Probability: {1-ai_prob:.6f}", file=sys.stderr)
            
            if ai_prob > 0.9:
                verdict = "AI Generated (High Confidence)"
            elif ai_prob > 0.7:
                verdict = "Likely AI"
            elif ai_prob < 0.3:
                verdict = "Real Image (High Confidence)"
            elif ai_prob < 0.5:
                verdict = "Likely Real"
            else:
                verdict = "Uncertain"
            
            print(f"  Verdict: {verdict}", file=sys.stderr)
            print()
        
        # Output just the number for easy parsing
        print(f"{ai_prob:.6f}")
        
    except Exception as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == '__main__':
    main()
