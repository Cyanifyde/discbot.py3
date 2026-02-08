# Quick Start Guide - AI Image Detector

## Installation (One Command)

```bash
pip install numpy scipy scikit-learn joblib Pillow opencv-python numba
```

## Usage (One Command)

```bash
python detect.py <image_path>
```

**Output:** Single number between 0.0 and 1.0
- **0.0** = Real image
- **1.0** = AI-generated
- **0.5** = Uncertain

## Examples

```bash
# Test local image
python detect.py my_photo.jpg
# Output: 0.123456  (low = likely real)

# Test with URL
python detect.py https://example.com/image.png
# Output: 0.987654  (high = likely AI)

# Verbose output
python detect.py image.png -v
# Shows detailed verdict
```

## Interpretation

| Score | Meaning |
|-------|---------|
| 0.00 - 0.30 | **Real Image** (high confidence) |
| 0.30 - 0.50 | **Likely Real** (medium confidence) |
| 0.50 - 0.70 | **Uncertain** (review manually) |
| 0.70 - 0.90 | **Likely AI** (medium confidence) |
| 0.90 - 1.00 | **AI Generated** (high confidence) |

## Discord Bot Example

```python
import discord
import subprocess

@bot.command()
async def check(ctx):
    if not ctx.message.attachments:
        return await ctx.send("Attach an image!")
    
    await ctx.message.attachments[0].save('temp.png')
    result = subprocess.run(['python', 'detect.py', 'temp.png'], 
                          capture_output=True, text=True)
    score = float(result.stdout.strip())
    
    if score > 0.9:
        await ctx.send(f"🤖 AI Generated ({score:.1%})")
    elif score < 0.3:
        await ctx.send(f"📷 Real Image ({(1-score):.1%})")
    else:
        await ctx.send(f"❓ Uncertain (AI: {score:.1%})")
```

## That's It!

See **README.md** for full documentation.
